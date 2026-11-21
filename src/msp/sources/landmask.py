"""Land mask and fixed offshore structures.

Land mask: Kartverket N50 map data (land-cover polygons: every area type except the sea, objtype 'Havflate').
Land is their union inside the study box; the land polygons are written as an ESRI Shapefile, then buffered so that piers, quays
and rocks right at the shore do not count as detections at sea.

Structures: aquaculture sites (Fiskeridirektoratet), petroleum facilities (Norwegian Offshore
Directorate, Sodir), and bridges, piers, breakwaters and overhead power lines over water
(OpenStreetMap). Fish farms and bridges are the main fixed bright targets in these waters.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import requests
from rasterio import features
from shapely.geometry import box
from shapely.ops import unary_union

USER_AGENT = "msp-research-client"
AQUACULTURE_URL = ("https://gis.fiskeridir.no/server/rest/services/fiskeridirWMS_akva/MapServer/3/query"
                   "?geometry={x0},{y0},{x1},{y1}&geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326"
                   "&where=1%3D1&outFields=*&f=geojson")
SODIR_URL = "https://factpages.sodir.no/downloads/shape/fclPoint.zip"


SLIVER_M2 = 50_000.0  # holes in the land union smaller than this are gaps between map polygons, not sea


def _fill_small_holes(geom, min_area: float):
    """Drop interior rings smaller than min_area (slivers where neighboring land polygons do not meet)."""
    from shapely.geometry import MultiPolygon, Polygon

    parts = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    out = [Polygon(p.exterior, [r for r in p.interiors if Polygon(r).area >= min_area]) for p in parts]
    return unary_union(out)


def build_land_mask(n50_zip: Path, bbox: list[float], crs: str, out_dir: Path, buffer_m: float) -> dict:
    """Write land.shp (unbuffered) and land_buffered.shp in the working projection.

    Land is the union of the N50 land-cover polygons (every area type except 'Havflate', sea). The N50
    county data stops some way offshore, so anything without a land polygon counts as sea; taking
    "box minus sea polygons" instead would turn the open sea beyond the data edge into land.
    """
    gdb = sorted({n.split("/")[0] for n in zipfile.ZipFile(n50_zip).namelist() if ".gdb" in n})[0]
    src = f"/vsizip/{Path(n50_zip).resolve().as_posix()}/{gdb}"
    area_ll = gpd.GeoSeries([box(*bbox)], crs="EPSG:4326")
    area_src = area_ll.to_crs("EPSG:25832")
    cover = pyogrio.read_dataframe(src, layer="N50_Arealdekke_omrade", bbox=tuple(area_src.total_bounds),
                                   where="objtype <> 'Havflate'", columns=["objtype"])
    area = area_ll.to_crs(crs).iloc[0]
    land = _fill_small_holes(unary_union(cover.to_crs(crs).geometry.values).intersection(area), SLIVER_M2)
    sea_union = area.difference(land)
    out_dir.mkdir(parents=True, exist_ok=True)
    land_gdf = gpd.GeoDataFrame({"kind": ["land"]}, geometry=[land], crs=crs).explode(index_parts=False)
    land_gdf = land_gdf[land_gdf.area > 0].reset_index(drop=True)
    land_gdf.to_file(out_dir / "land.shp", driver="ESRI Shapefile")
    buffered = gpd.GeoDataFrame({"kind": ["land_buffered"], "buffer_m": [buffer_m]},
                                geometry=[unary_union(land_gdf.geometry.values).buffer(buffer_m)], crs=crs)
    buffered.to_file(out_dir / "land_buffered.shp", driver="ESRI Shapefile")
    return {"land_polygons": int(len(land_gdf)), "sea_share": round(sea_union.area / area.area, 4)}


def fetch_structures(bbox: list[float], crs: str, out: Path, land_shp: Path | None = None) -> dict:
    """Fish farms at sea and offshore facilities (points, no owner fields), plus bridges, piers,
    breakwaters and overhead power lines over water (lines) when the land mask is given, as GeoParquet."""
    x0, y0, x1, y1 = bbox
    r = requests.get(AQUACULTURE_URL.format(x0=x0, y0=y0, x1=x1, y1=y1), timeout=120,
                     headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    aq = gpd.read_file(io.BytesIO(r.content))
    place_col = next((c for c in aq.columns if c.lower() == "plassering"), None)
    if place_col:
        aq = aq[aq[place_col].astype(str).str.upper().str.startswith("SJ")]
    aq = gpd.GeoDataFrame({"kind": ["aquaculture site"] * len(aq), "source": ["Fiskeridirektoratet"] * len(aq)},
                          geometry=aq.geometry.values, crs=aq.crs or "EPSG:4326")
    r = requests.get(SODIR_URL, timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = out.parent / "sodir_facilities.zip"
    tmp_zip.write_bytes(r.content)
    with zipfile.ZipFile(tmp_zip) as zf:
        shp = next(n for n in zf.namelist() if n.lower().endswith(".shp"))
    fac = gpd.read_file(f"/vsizip/{tmp_zip.resolve().as_posix()}/{shp}")
    tmp_zip.unlink()
    fac = fac.to_crs("EPSG:4326")
    fac = fac[fac.intersects(box(*bbox))]
    kind_col = next((c for c in fac.columns if c.lower().startswith("fclkind")), None)
    kinds = fac[kind_col].astype(str).str.lower().tolist() if kind_col else ["facility"] * len(fac)
    fac = gpd.GeoDataFrame({"kind": kinds, "source": ["Sodir"] * len(fac)},
                           geometry=fac.geometry.values, crs="EPSG:4326")
    allp = gpd.GeoDataFrame(
        {"kind": list(aq["kind"]) + list(fac["kind"]), "source": list(aq["source"]) + list(fac["source"])},
        geometry=list(aq.geometry.values) + list(fac.geometry.values), crs="EPSG:4326").to_crs(crs)
    allp["geometry"] = allp.geometry.centroid
    lines = fetch_linear_structures(bbox, crs, land_shp) if land_shp is not None else None
    if lines is not None and len(lines):
        allp = gpd.GeoDataFrame(pd.concat([allp, lines], ignore_index=True), crs=crs)
    out.parent.mkdir(parents=True, exist_ok=True)
    allp.to_parquet(out)
    counts = {"aquaculture_sites": int(len(aq)), "facilities": int(len(fac))}
    if lines is not None:
        counts.update({k.replace(" ", "_") + "s": int(v) for k, v in lines["kind"].value_counts().items()})
    return counts


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Fixed structures over water that are bright to radar: bridges, piers, breakwaters, overhead power lines
OVERPASS_QUERY = """[out:json][timeout:180];
(
  way["bridge"]["bridge"!="no"]({s},{w},{n},{e});
  way["man_made"~"^(bridge|pier|breakwater|groyne)$"]({s},{w},{n},{e});
  way["power"="line"]({s},{w},{n},{e});
);
out geom;"""


def _kind(tags: dict) -> str:
    if tags.get("power") == "line":
        return "overhead power line"
    mm = tags.get("man_made")
    if mm in ("pier", "breakwater", "groyne"):
        return mm
    return "bridge"


def fetch_linear_structures(bbox: list[float], crs: str, land_shp: Path) -> gpd.GeoDataFrame:
    """Bridges, piers, breakwaters and overhead power lines from OpenStreetMap (ODbL), kept only where
    some part lies over water (the rest is already masked as land)."""
    from shapely.geometry import LineString, Polygon

    w, s, e, n = bbox
    r = requests.post(OVERPASS_URL, data={"data": OVERPASS_QUERY.format(s=s, w=w, n=n, e=e)}, timeout=300,
                      headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    rows = []
    for el in r.json().get("elements", []):
        pts = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        if len(pts) < 2:
            continue
        tags = el.get("tags", {})
        closed = pts[0] == pts[-1] and len(pts) >= 4 and tags.get("man_made") in ("bridge", "pier", "breakwater")
        rows.append({"kind": _kind(tags), "geometry": Polygon(pts) if closed else LineString(pts)})
    if not rows:
        return gpd.GeoDataFrame({"kind": [], "source": []}, geometry=[], crs=crs)
    lines = gpd.GeoDataFrame(rows, crs="EPSG:4326").to_crs(crs)
    land = unary_union(gpd.read_file(land_shp).to_crs(crs).geometry.values)
    over_water = lines.geometry.difference(land)
    lines = lines[~over_water.is_empty & (over_water.length + over_water.area > 1.0)].copy()
    lines["source"] = "OpenStreetMap"
    return lines[["kind", "source", "geometry"]].reset_index(drop=True)


def sea_mask_for_grid(land_shp: Path, structures: Path | None, transform, width: int, height: int,
                      structure_buffer_m: float = 150.0) -> np.ndarray:
    """Boolean array on a raster grid: True where a detection may be kept (open sea)."""
    land = gpd.read_file(land_shp)
    shapes = list(land.geometry.values)
    if structures is not None and Path(structures).exists():
        st = gpd.read_parquet(structures)
        shapes += list(st.geometry.buffer(structure_buffer_m).values)
    blocked = features.rasterize(((g, 1) for g in shapes), out_shape=(height, width), transform=transform,
                                 fill=0, dtype="uint8", all_touched=True)
    # Only the study box counts (the land mask and the AIS queries cover the box, nothing outside it)
    from msp.config import load_config

    cfg = load_config()
    area = gpd.GeoSeries([box(*cfg.area.bbox)], crs="EPSG:4326").to_crs(land.crs).iloc[0]
    inside = features.rasterize([(area, 1)], out_shape=(height, width), transform=transform, fill=0, dtype="uint8")
    return (blocked == 0) & (inside == 1)


def sea_mask_like(raster: Path, land_shp: Path, structures: Path | None) -> np.ndarray:
    with rasterio.open(raster) as src:
        return sea_mask_for_grid(land_shp, structures, src.transform, src.width, src.height)
