"""MET Norway archived wave and wind fields (NetCDF over OPeNDAP from thredds.met.no).

Source: MyWaveWAM 800 m Vestlandet hindcast/forecast aggregation. It carries significant wave height
(hs), wave direction (thq, direction the waves travel TO) and the 10 m wind that drives the model
(ff speed, dd direction the wind blows TO). We subset the study area and period and write one
CF-compliant NetCDF file with conventional FROM directions and wind components.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

WAVE_URL = "https://thredds.met.no/thredds/dodsC/fou-hi/mywavewam800v_curr_be"
VARS = ["hs", "thq", "ff", "dd"]


def _bbox_index(ds: xr.Dataset, bbox: list[float]) -> tuple[slice, slice]:
    lon, lat = ds["longitude"].values, ds["latitude"].values
    inside = (lon >= bbox[0]) & (lon <= bbox[2]) & (lat >= bbox[1]) & (lat <= bbox[3])
    rows, cols = np.where(inside)
    return slice(rows.min(), rows.max() + 1), slice(cols.min(), cols.max() + 1)


# Daily analysis files of the same model (hourly fields from 18 UTC to 17 UTC next day), back to 2023.
# The aggregated "best estimate" view above is slow to open; the daily files are the same data.
DAILY_URL = "https://thredds.met.no/thredds/dodsC/fou-hi/mywavewam800v_currhf/mywavewam800_vestlandet.an.{d:%Y%m%d}18.nc"


class MissingDay(Exception):
    """The archive has no file for this day (a gap on the provider's side)."""


def _open_daily(d: date) -> xr.Dataset:
    for attempt in range(1, 5):
        try:
            return xr.open_dataset(DAILY_URL.format(d=d))
        except OSError as exc:
            if "not found" in str(exc).lower():
                raise MissingDay(f"{d:%Y-%m-%d}18") from exc
            if attempt == 4:
                raise
            time.sleep(15 * attempt)
    raise RuntimeError("unreachable")


def fetch_waves(bbox: list[float], start: date, end: date, out: Path, step_hours: int = 1,
                log=print) -> dict:
    """Download hourly fields for start..end (resumable per daily file) and write one CF NetCDF file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    parts_dir = out.parent / (out.stem + "_parts")
    parts_dir.mkdir(exist_ok=True)
    t0 = time.time()
    first = _open_daily(start - timedelta(days=1))
    rs, cs = _bbox_index(first, bbox)
    lat = first["latitude"].isel(rlat=rs, rlon=cs).values
    lon = first["longitude"].isel(rlat=rs, rlon=cs).values
    parts, missing = [], []
    day = start - timedelta(days=1)  # the file of day D-1 18 UTC holds 00 to 17 UTC of day D
    while day <= end:
        part = parts_dir / f"{day:%Y%m%d}18.nc"
        if not part.exists():
            for attempt in range(1, 4):
                try:
                    sel = _open_daily(day)[VARS].isel(rlat=rs, rlon=cs).isel(time=slice(None, None, step_hours))
                    sel.load().to_netcdf(part)
                    log(f"waves {day:%Y-%m-%d}18 ok")
                    break
                except MissingDay:
                    missing.append(f"{day:%Y-%m-%d}18")
                    log(f"waves {day:%Y-%m-%d}18 missing in the archive (those hours stay without sea state)")
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(10 * attempt)
        if part.exists():
            parts.append(part)
        day += timedelta(days=1)
    ds = xr.concat([xr.load_dataset(p) for p in parts], dim="time")
    ds = ds.sortby("time").drop_duplicates("time")
    ds = ds.sel(time=slice(f"{start}T00:00", f"{end}T23:59"))
    write_cf(ds, lat, lon, out)
    return {"file": out.name, "bytes": out.stat().st_size, "seconds": round(time.time() - t0, 1),
            "times": int(ds.sizes["time"]), "missing_days": missing}


def write_cf(ds: xr.Dataset, lat: np.ndarray, lon: np.ndarray, out: Path) -> None:
    """Rename to CF standard names, convert TO directions to FROM directions, add wind components."""
    wave_from = (ds["thq"] + 180.0) % 360.0
    wind_from = (ds["dd"] + 180.0) % 360.0
    rad = np.deg2rad(wind_from)
    u = -ds["ff"] * np.sin(rad)
    v = -ds["ff"] * np.cos(rad)
    res = xr.Dataset(
        {
            "hs": ds["hs"].astype("float32").assign_attrs(
                standard_name="sea_surface_wave_significant_height", long_name="Significant wave height", units="m"),
            "wave_dir": wave_from.astype("float32").assign_attrs(
                standard_name="sea_surface_wave_from_direction", long_name="Mean wave direction (from)",
                units="degree"),
            "wind_speed": ds["ff"].astype("float32").assign_attrs(
                standard_name="wind_speed", long_name="10 m wind speed", units="m s-1"),
            "wind_dir": wind_from.astype("float32").assign_attrs(
                standard_name="wind_from_direction", long_name="10 m wind direction (from)", units="degree"),
            "u10": u.astype("float32").assign_attrs(standard_name="eastward_wind", units="m s-1"),
            "v10": v.astype("float32").assign_attrs(standard_name="northward_wind", units="m s-1"),
        },
        coords={
            "time": ds["time"],
            "rlat": ds["rlat"].assign_attrs(standard_name="grid_latitude", units="degrees"),
            "rlon": ds["rlon"].assign_attrs(standard_name="grid_longitude", units="degrees"),
            "lat": (("rlat", "rlon"), lat.astype("float32"), {"standard_name": "latitude", "units": "degrees_north"}),
            "lon": (("rlat", "rlon"), lon.astype("float32"), {"standard_name": "longitude", "units": "degrees_east"}),
        },
    )
    for name in res.data_vars:
        res[name].attrs["coordinates"] = "lat lon"
        res[name].attrs["grid_mapping"] = "rotated_pole"
    res["rotated_pole"] = xr.DataArray(np.int32(0), attrs={
        "grid_mapping_name": "rotated_latitude_longitude",
        "grid_north_pole_latitude": 22.0, "grid_north_pole_longitude": -8.0 + 180.0,
        "proj4": "+proj=ob_tran +o_proj=longlat +lon_0=-8 +o_lat_p=22 +R=6.371e+06",
    })
    res.attrs = {
        "Conventions": "CF-1.8",
        "title": "Significant wave height, wave direction and 10 m wind over the study area",
        "source": "MET Norway MyWaveWAM 800 m (Vestlandet), thredds.met.no",
        "institution": "Norwegian Meteorological Institute (data); processing by this project",
        "license": "NLOD 2.0 and CC BY 4.0. Data from The Norwegian Meteorological Institute",
        "comment": "Directions converted from 'to' to 'from'. Wind is the model's 10 m forcing wind.",
    }
    enc = {v: {"zlib": True, "complevel": 4} for v in res.data_vars if v != "rotated_pole"}
    res.to_netcdf(out, encoding=enc)
