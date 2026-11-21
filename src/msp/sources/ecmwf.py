"""ECMWF open data forecasts (GRIB2) from the public archive mirrors (Google Cloud, then AWS).

ECMWF publishes its open forecast data (IFS, 0.25 degree, CC BY 4.0). The ECMWF portal keeps only the
latest runs, so the replay flow reads forecasts issued inside the study period from the public mirror.
Only the needed fields are fetched, using the byte offsets listed in each forecast's .index file:
10 m wind (10u, 10v) and mean sea level pressure (msl) from the atmospheric stream, significant wave
height (swh) and mean wave direction (mwd) from the wave stream.

Replay: the pipeline runs as if it were live on a replay clock. A run counts as published
PUBLISH_DELAY after its nominal time, and freshness is measured against the replay clock.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import requests
import xarray as xr

MIRRORS = ["https://storage.googleapis.com/ecmwf-open-data",
           "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"]
USER_AGENT = "msp-research-client"
STREAMS = {"oper": ["10u", "10v", "msl"], "wave": ["swh", "mwd"]}
PUBLISH_DELAY = timedelta(hours=8)


def run_path(run: datetime, step: int, stream: str, ext: str) -> str:
    d = run.strftime("%Y%m%d")
    hh = run.strftime("%H")
    return f"{d}/{hh}z/ifs/0p25/{stream}/{d}{hh}0000-{step}h-{stream}-fc.{ext}"


def _get(path: str, headers: dict | None = None, retries: int = 4) -> bytes:
    """GET from the first mirror that answers; backs off on throttling and error bodies."""
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    for base in MIRRORS:
        for attempt in range(1, retries + 1):
            r = requests.get(f"{base}/{path}", headers=h, timeout=120)
            if r.status_code in (200, 206) and not r.content[:60].lstrip().startswith(b"<?xml"):
                return r.content
            if r.status_code == 404:
                break
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"no mirror served {path}")


def fetch_run(run: datetime, steps: list[int], out_dir: Path) -> list[dict]:
    """Download the needed GRIB2 messages of one run into one file per step. Skips existing files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for step in steps:
        out = out_dir / f"ifs_{run:%Y%m%d%H}_{step:03d}h.grib2"
        if out.exists() and out.stat().st_size > 0:
            results.append({"file": out.name, "bytes": out.stat().st_size, "cached": True})
            continue
        chunks = []
        for stream, params in STREAMS.items():
            index = _get(run_path(run, step, stream, "index")).decode().splitlines()
            for line in index:
                entry = json.loads(line)
                if entry.get("param") in params:
                    start = int(entry["_offset"])
                    end = start + int(entry["_length"]) - 1
                    chunks.append(_get(run_path(run, step, stream, "grib2"), {"Range": f"bytes={start}-{end}"}))
        tmp = out.with_suffix(".part")
        tmp.write_bytes(b"".join(chunks))
        tmp.replace(out)
        results.append({"file": out.name, "bytes": out.stat().st_size, "cached": False})
    return results


def read_grib(path: Path, bbox: list[float], margin: float = 0.5) -> xr.Dataset:
    """Read one GRIB2 file with cfgrib and crop it to the study area. Returns a CF-style dataset."""
    x0, y0, x1, y1 = bbox
    parts = []
    for flt in ({"typeOfLevel": "heightAboveGround", "level": 10}, {"typeOfLevel": "meanSea"},
                {"typeOfLevel": "surface"}):
        try:
            ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"filter_by_keys": flt, "indexpath": ""})
        except Exception:
            continue
        if not ds.data_vars:
            continue
        lon = ds["longitude"]
        ds = ds.assign_coords(longitude=((lon + 180) % 360) - 180).sortby("longitude")
        ds = ds.sel(latitude=slice(y1 + margin, y0 - margin), longitude=slice(x0 - margin, x1 + margin))
        drop = [c for c in ("heightAboveGround", "meanSea", "surface", "number") if c in ds.coords]
        parts.append(ds.drop_vars(drop).load())
    if not parts:
        raise ValueError(f"no fields read from {path.name}")
    return xr.merge(parts, compat="override")


def to_cf(ds: xr.Dataset) -> xr.Dataset:
    out = xr.Dataset(coords={"latitude": ds["latitude"], "longitude": ds["longitude"]})
    if "u10" in ds and "v10" in ds:
        out["u10"] = ds["u10"].astype("float32").assign_attrs(standard_name="eastward_wind", units="m s-1")
        out["v10"] = ds["v10"].astype("float32").assign_attrs(standard_name="northward_wind", units="m s-1")
        spd = np.hypot(ds["u10"], ds["v10"])
        out["wind_speed"] = spd.astype("float32").assign_attrs(standard_name="wind_speed", units="m s-1")
    if "msl" in ds:
        out["msl"] = ds["msl"].astype("float32").assign_attrs(standard_name="air_pressure_at_mean_sea_level", units="Pa")
    if "swh" in ds:
        out["swh"] = ds["swh"].astype("float32").assign_attrs(
            standard_name="sea_surface_wave_significant_height", units="m")
    if "mwd" in ds:
        out["mwd"] = ds["mwd"].astype("float32").assign_attrs(standard_name="sea_surface_wave_from_direction",
                                                             units="degree")
    for c in ("time", "step", "valid_time"):
        if c in ds.coords:
            out = out.assign_coords({c: ds[c]})
    out["latitude"].attrs.update(standard_name="latitude", units="degrees_north")
    out["longitude"].attrs.update(standard_name="longitude", units="degrees_east")
    out.attrs = {"Conventions": "CF-1.8", "source": "ECMWF IFS open data, 0.25 degree (GRIB2)",
                 "license": "CC BY 4.0. Source www.ecmwf.int. Cropped to the study area and converted to NetCDF."}
    return out


def runs_in_period(start: datetime, end: datetime, hours=(0, 12)) -> list[datetime]:
    runs, t = [], start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
    while t <= end:
        for h in hours:
            r = t.replace(hour=h)
            if start <= r <= end:
                runs.append(r)
        t += timedelta(days=1)
    return runs


def latest_published_run(replay_clock: datetime, hours=(0, 12)) -> datetime:
    """Newest run that would have been published at the replay clock time."""
    t = replay_clock - PUBLISH_DELAY
    cands = [t.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
    cands = [c for c in cands if c <= t] or [(t - timedelta(days=1)).replace(hour=max(hours), minute=0,
                                                                          second=0, microsecond=0)]
    return max(cands)
