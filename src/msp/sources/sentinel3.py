"""Sentinel-3 SLSTR sea surface temperature (L2P, NetCDF) from the Copernicus Data Space Ecosystem.

Each SL_2_WST product holds one GHRSST L2P NetCDF file (HDF5-based NetCDF-4) on the satellite swath grid.
We keep pixels with quality_level 4 or 5 (acceptable, best), crop to the study area, regrid to a regular
0.01 degree grid by nearest neighbour, and write a CF-compliant NetCDF plus a COG preview.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from rasterio.transform import from_origin
from scipy.spatial import cKDTree
from shapely import wkt
from shapely.geometry import box

from msp.sources.cdse import CATALOGUE, DOWNLOAD, USER_AGENT, CdseSession

GRID_STEP = 0.01


def search_wst(bbox: list[float], start: str, end: str) -> list[dict]:
    x0, y0, x1, y1 = bbox
    aoi = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
    flt = ("Collection/Name eq 'SENTINEL-3' and "
           "Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/OData.CSC.StringAttribute/Value eq 'SL_2_WST___') and "
           f"OData.CSC.Intersects(area=geography'SRID=4326;{aoi}') and "
           f"ContentDate/Start gt {start}T00:00:00.000Z and ContentDate/Start lt {end}T23:59:59.999Z")
    out, url = [], CATALOGUE
    params = {"$filter": flt, "$orderby": "ContentDate/Start asc", "$top": "1000"}
    r = requests.get(url, params=params, timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    area = box(*bbox)
    for p in r.json()["value"]:
        g = wkt.loads(p["Footprint"].split(";", 1)[1].rstrip("'"))
        out.append({"id": p["Id"], "name": p["Name"], "start": p["ContentDate"]["Start"],
                    "size": int(p["ContentLength"]), "coverage": round(g.intersection(area).area / area.area, 3),
                    "timeliness": "NT" if "_NT_" in p["Name"] else ("NR" if "_NR_" in p["Name"] else "ST")})
    return out


def pick_daily(products: list[dict], every_n_days: int = 1) -> list[dict]:
    """One product per day: prefer non-time-critical (NT) processing, then the best area coverage."""
    best: dict[str, dict] = {}
    rank = {"NT": 0, "ST": 1, "NR": 2}
    for p in products:
        day = p["start"][:10]
        key = (rank[p["timeliness"]], -p["coverage"])
        if p["coverage"] >= 0.5 and (day not in best or key < (rank[best[day]["timeliness"]], -best[day]["coverage"])):
            best[day] = p
    days = sorted(best)
    return [best[d] for i, d in enumerate(days) if i % every_n_days == 0]


def download(product: dict, out: Path, session: CdseSession) -> dict:
    if out.exists() and out.stat().st_size == product["size"]:
        return {"bytes": product["size"], "cached": True}
    out.parent.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {session.token()}", "User-Agent": USER_AGENT})
    url = DOWNLOAD.format(id=product["id"])
    r = s.get(url, allow_redirects=False, stream=True, timeout=120)
    while r.status_code in (301, 302, 303, 307, 308):
        url = r.headers["Location"]
        r.close()
        r = s.get(url, allow_redirects=False, stream=True, timeout=120)
    r.raise_for_status()
    t0, n = time.time(), 0
    tmp = out.with_suffix(".part")
    with r, open(tmp, "wb") as f:
        for b in r.iter_content(4 << 20):
            f.write(b)
            n += len(b)
    tmp.replace(out)
    return {"bytes": n, "seconds": round(time.time() - t0, 1), "cached": False}


def regular_grid(bbox: list[float], step: float = GRID_STEP) -> tuple[np.ndarray, np.ndarray]:
    lons = np.round(np.arange(bbox[0] + step / 2, bbox[2], step), 5)
    lats = np.round(np.arange(bbox[3] - step / 2, bbox[1], -step), 5)
    return lons, lats


def sst_to_grid(zip_path: Path, bbox: list[float], work: Path) -> xr.Dataset | None:
    """Extract the L2P NetCDF, keep good-quality pixels in the box and regrid to 0.01 degree."""
    with zipfile.ZipFile(zip_path) as zf:
        ncs = [n for n in zf.namelist() if n.endswith(".nc")]
        # The NADIR file covers the full 1400 km swath; the DUAL-view file only the central part
        name = next((n for n in ncs if "NADIR" in n.upper()), ncs[0])
        work.mkdir(parents=True, exist_ok=True)
        nc = work / Path(name).name
        nc.write_bytes(zf.read(name))
    try:
        ds = xr.open_dataset(nc, engine="h5netcdf", mask_and_scale=True)
        lat = ds["lat"].values
        lon = ds["lon"].values
        sst = ds["sea_surface_temperature"].isel(time=0).values if "time" in ds["sea_surface_temperature"].dims \
            else ds["sea_surface_temperature"].values
        ql = ds["quality_level"].isel(time=0).values if "time" in ds["quality_level"].dims else ds["quality_level"].values
        t = ds["time"].values[0] if "time" in ds else None
        ds.close()
    finally:
        nc.unlink(missing_ok=True)
    m = (lon >= bbox[0] - 0.05) & (lon <= bbox[2] + 0.05) & (lat >= bbox[1] - 0.05) & (lat <= bbox[3] + 0.05)
    good = m & np.isfinite(sst) & (ql >= 4)
    lons, lats = regular_grid(bbox)
    gx, gy = np.meshgrid(lons, lats)
    out = np.full(gx.shape, np.nan, dtype=np.float32)
    if good.sum() > 20:
        coslat = np.cos(np.deg2rad(61.0))
        tree = cKDTree(np.column_stack([lon[good] * coslat, lat[good]]))
        d, idx = tree.query(np.column_stack([gx.ravel() * coslat, gy.ravel()]), distance_upper_bound=0.015)
        ok = np.isfinite(d)
        vals = np.full(gx.size, np.nan, dtype=np.float32)
        vals[ok] = sst[good][idx[ok]]
        out = vals.reshape(gx.shape)
    res = xr.Dataset(
        {"sst": (("lat", "lon"), out, {"standard_name": "sea_surface_skin_temperature", "units": "K",
                                       "long_name": "Sea surface skin temperature (quality level 4 and 5)"})},
        coords={"lat": ("lat", lats, {"standard_name": "latitude", "units": "degrees_north"}),
                "lon": ("lon", lons, {"standard_name": "longitude", "units": "degrees_east"})},
        attrs={"Conventions": "CF-1.8", "title": "Sea surface temperature over the study area",
               "source": "Sentinel-3 SLSTR L2P sea surface temperature (SL_2_WST, NT)",
               "license": "Contains modified Copernicus Sentinel data 2026",
               "comment": "Nearest-neighbour regridding to 0.01 degree; pixels with quality_level below 4 removed."})
    if t is not None:
        res = res.expand_dims(time=[np.datetime64(t, "ns")])
        res["time"].attrs.update(standard_name="time")
    return res


def write_netcdf(ds: xr.Dataset, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out, engine="h5netcdf", encoding={"sst": {"zlib": True, "complevel": 4}})


def grid_transform(bbox: list[float], step: float = GRID_STEP):
    return from_origin(bbox[0], bbox[3], step, step)
