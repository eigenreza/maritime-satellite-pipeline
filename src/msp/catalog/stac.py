"""STAC catalogue of every derived product.

STAC (SpatioTemporal Asset Catalog) is a standard JSON format that describes geospatial data: where an
item is, when it was acquired, and which files (assets) belong to it. Items here use STAC core plus the
projection, raster, SAR, satellite, electro-optical and processing extensions where they apply.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pystac
import rasterio
from pyproj import Transformer
from pystac.extensions.eo import EOExtension
from pystac.extensions.projection import ProjectionExtension
from pystac.extensions.raster import DataType, RasterBand, RasterExtension
from pystac.extensions.sar import FrequencyBand, Polarization, SarExtension
from pystac.extensions.sat import OrbitState, SatExtension
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shp_transform

from msp import __version__, paths

PROCESSING_SCHEMA = "https://stac-extensions.github.io/processing/v1.2.0/schema.json"
LICENSES = {
    "sentinel": "Contains modified Copernicus Sentinel data 2025 and 2026",
    "metno": "Data from The Norwegian Meteorological Institute (NLOD 2.0 / CC BY 4.0)",
    "ecmwf": "Copyright European Centre for Medium-Range Weather Forecasts (ECMWF), CC BY 4.0",
    "kartverket": "(c) Kartverket, CC BY 4.0",
    "nlod": "Contains data under the Norwegian licence for Open Government data (NLOD)",
    "osm": "Bridges, piers, breakwaters and power lines (c) OpenStreetMap contributors, ODbL 1.0",
}

COLLECTIONS = {
    "sentinel-1-sigma0": ("Sentinel-1 calibrated radar backscatter",
                          "Calibrated, noise-corrected and terrain-corrected Sentinel-1 IW GRD backscatter "
                          "(sigma0, VV and VH) over the study area, 20 m, cloud-optimized GeoTIFF.", "sentinel"),
    "ship-detections": ("Radar ship detections",
                        "Bright objects at sea found by the CFAR detector in each Sentinel-1 pass, with shape, "
                        "brightness, AIS match, sea state, ship probability and estimated length (GeoParquet).",
                        "sentinel"),
    "sentinel-2-cloud": ("Sentinel-2 cloud cover over sea",
                         "Cloud mask from the Sentinel-2 L2A scene classification and a true-color preview "
                         "per acquisition day, mosaicked over the study area.", "sentinel"),
    "sentinel-3-sst": ("Sentinel-3 sea surface temperature",
                       "Sentinel-3 SLSTR L2 sea surface temperature cropped to the study area, CF NetCDF "
                       "with a cloud-optimized GeoTIFF preview.", "sentinel"),
    "metocean": ("Wind and waves",
                 "Hourly significant wave height, wave direction and 10 m wind from the MET Norway wave model "
                 "(NetCDF), and ECMWF forecasts (from GRIB2) for the study period.", "metno"),
    "reference-layers": ("Reference layers",
                         "Land mask (Shapefile) and fixed offshore structures (GeoParquet) used to keep "
                         "detections at sea.", "kartverket"),
}


def _ll_geometry(bounds, crs: str) -> dict:
    t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    g = shp_transform(lambda x, y, z=None: t.transform(x, y), box(*bounds))
    return mapping(g)


def _rel(path: Path, base: Path) -> str:
    return Path(__import__("os").path.relpath(Path(path).resolve(), base.resolve())).as_posix()


def _processing(item: pystac.Item, software: dict, lineage: str, level: str = "L2") -> None:
    if PROCESSING_SCHEMA not in item.stac_extensions:
        item.stac_extensions.append(PROCESSING_SCHEMA)
    item.properties["processing:level"] = level
    item.properties["processing:software"] = software | {"maritime-satellite-pipeline": __version__}
    item.properties["processing:lineage"] = lineage


def raster_item(item_id: str, cog: Path, dt: datetime, collection: str, item_dir: Path,
                start: datetime | None = None, end: datetime | None = None,
                bands: list[dict] | None = None, extra: dict | None = None) -> pystac.Item:
    with rasterio.open(cog) as src:
        bounds, crs, shp, tr = src.bounds, src.crs.to_string(), [src.height, src.width], list(src.transform)[:6]
        epsg = src.crs.to_epsg()
        dtype = src.dtypes[0]
        nodata = src.nodata
    geom = _ll_geometry(bounds, crs)
    item = pystac.Item(id=item_id, geometry=geom, bbox=list(shape(geom).bounds), datetime=dt,
                       properties={}, collection=collection,
                       start_datetime=start, end_datetime=end)
    item.add_asset("data", pystac.Asset(href=_rel(cog, item_dir), media_type=pystac.MediaType.COG,
                                        roles=["data"], title=cog.name))
    proj = ProjectionExtension.ext(item, add_if_missing=True)
    proj.epsg = epsg
    proj.shape = shp
    proj.transform = tr
    proj.bbox = list(bounds)
    if bands:
        rb = RasterExtension.ext(item.assets["data"], add_if_missing=True)
        out_bands = []
        for b in bands:
            b = dict(b)
            labels = {k: b.pop(k) for k in ("name", "description") if k in b}
            nd = "nan" if nodata is not None and nodata != nodata else nodata
            band = RasterBand.create(nodata=nd, data_type=DataType(dtype), **b)
            band.properties.update(labels)
            out_bands.append(band)
        rb.bands = out_bands
    item.properties.update(extra or {})
    return item


def s1_item(pass_meta: dict, cog: Path, engine: str, item_dir: Path) -> pystac.Item:
    item = raster_item(f"{pass_meta['pass_id']}_sigma0", cog, pass_meta["start_time"], "sentinel-1-sigma0",
                       item_dir, start=pass_meta["start_time"], end=pass_meta["stop_time"],
                       bands=[{"name": "VV", "scale": 0.01, "offset": 0.0, "unit": "dB"},
                              {"name": "VH", "scale": 0.01, "offset": 0.0, "unit": "dB"}],
                       extra={"platform": "sentinel-1" + pass_meta["platform"][2].lower(),
                              "constellation": "sentinel-1", "instruments": ["c-sar"]})
    sar = SarExtension.ext(item, add_if_missing=True)
    sar.apply(instrument_mode="IW", frequency_band=FrequencyBand.C, polarizations=[Polarization.VV, Polarization.VH],
              product_type="GRD", center_frequency=5.405, resolution_range=20.0, resolution_azimuth=22.0,
              pixel_spacing_range=10.0, pixel_spacing_azimuth=10.0, looks_range=5, looks_azimuth=1)
    sat = SatExtension.ext(item, add_if_missing=True)
    sat.relative_orbit = int(pass_meta["relative_orbit"])
    sat.orbit_state = OrbitState.DESCENDING if pass_meta["orbit_direction"].upper().startswith("DESC") \
        else OrbitState.ASCENDING
    sw = {"ESA SNAP": "14.0"} if engine == "snap" else {"GDAL": rasterio.__gdal_version__}
    lineage = ("Sentinel-1 IW GRD SAFE; precise orbit, thermal noise removal, sigma0 calibration, subset and "
               "terrain correction (Copernicus 30 m DEM) with the SNAP graph s1_grd_sigma0.xml; averaged to 20 m "
               "and stored in dB." if engine == "snap" else
               "Sentinel-1 IW GRD SAFE; thermal noise removal and sigma0 calibration from the annotation lookup "
               "tables, geocoded with the scene's ground control points (GDAL warp); averaged to 20 m, in dB.")
    _processing(item, sw, lineage)
    item.properties["license"] = "proprietary"
    item.properties["msp:attribution"] = LICENSES["sentinel"]
    return item


def vector_item(item_id: str, href: Path, dt: datetime | None, collection: str, geometry: dict,
                item_dir: Path, media_type: str, props: dict | None = None,
                start: datetime | None = None, end: datetime | None = None, roles=("data",)) -> pystac.Item:
    item = pystac.Item(id=item_id, geometry=geometry, bbox=list(shape(geometry).bounds), datetime=dt,
                       properties=dict(props or {}), collection=collection, start_datetime=start,
                       end_datetime=end)
    item.add_asset("data", pystac.Asset(href=_rel(href, item_dir), media_type=media_type, roles=list(roles),
                                        title=Path(href).name))
    return item


def build_catalog(items: list[pystac.Item], root: Path, period: tuple[datetime, datetime],
                  bbox: list[float]) -> pystac.Catalog:
    cat = pystac.Catalog(id="maritime-satellite-pipeline",
                         description="Derived products of the maritime satellite pipeline: radar backscatter, ship "
                                     "detections, cloud cover, sea surface temperature, wind and waves over the "
                                     "waters off Bergen and Fedje, western Norway.",
                         title="Maritime satellite pipeline catalogue")
    by_col: dict[str, list[pystac.Item]] = {}
    for it in items:
        by_col.setdefault(it.collection_id, []).append(it)
    for cid, its in sorted(by_col.items()):
        title, desc, lic = COLLECTIONS[cid]
        extent = pystac.Extent(pystac.SpatialExtent([bbox]), pystac.TemporalExtent([[period[0], period[1]]]))
        col = pystac.Collection(id=cid, description=desc, extent=extent, title=title, license="other",
                                providers=[pystac.Provider(name="Reza Azad Gholami",
                                                           roles=[pystac.ProviderRole.PROCESSOR])])
        col.extra_fields["msp:attribution"] = LICENSES[lic]
        for it in sorted(its, key=lambda i: i.id):
            it.collection_id = None
            col.add_item(it)
        cat.add_child(col)
    cat.normalize_hrefs(str(root))
    return cat


def save(cat: pystac.Catalog) -> None:
    cat.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)


def item_index(cat: pystac.Catalog) -> list[dict]:
    """Flat list of items for the GeoParquet index and the demo's catalogue search."""
    rows = []
    for it in cat.get_items(recursive=True):
        rows.append({"id": it.id, "collection": it.get_collection().id if it.get_collection() else None,
                     "datetime": (it.datetime or it.common_metadata.start_datetime).isoformat(),
                     "bbox": it.bbox, "geometry": json.dumps(it.geometry),
                     "properties": json.dumps(it.properties, default=str)})
    return rows


def write_index(rows: list[dict], out: Path) -> None:
    import geopandas as gpd

    gdf = gpd.GeoDataFrame(rows, geometry=[shape(json.loads(r["geometry"])) for r in rows], crs="EPSG:4326")
    gdf = gdf.drop(columns=["geometry"]).set_geometry(gpd.GeoSeries([shape(json.loads(r["geometry"])) for r in rows],
                                                                    crs="EPSG:4326"))
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out)


__all__ = ["s1_item", "raster_item", "vector_item", "build_catalog", "save", "item_index", "write_index",
           "paths", "EOExtension"]
