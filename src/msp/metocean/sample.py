"""Wind and wave values at points and times (detections, AIS ships, map clicks).

Nearest model sea cell in space (KD-tree on the curvilinear grid) and nearest hour in time. A point
farther than `metocean.max_cell_distance_m` from every valid model sea cell (up a narrow fjord, on land,
outside the grid) gets no value: its sea state is missing, never zero.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy.spatial import cKDTree

from msp import paths
from msp.config import load_config

WAVES_FILE = paths.PRODUCTS / "metocean" / "metno_waves_wind.nc"
M_PER_DEG = 111_320.0


def _max_cell_distance_deg() -> float:
    return float((load_config().get("metocean") or {}).get("max_cell_distance_m", 2000)) / M_PER_DEG


@lru_cache(maxsize=2)
def _load(path: str):
    ds = xr.load_dataset(path)
    lat, lon = ds["lat"].values, ds["lon"].values
    sea = np.isfinite(ds["hs"].isel(time=0).values)
    coslat = np.cos(np.deg2rad(60.6))
    tree = cKDTree(np.column_stack([lon[sea] * coslat, lat[sea]]))
    rows, cols = np.nonzero(sea)
    return ds, tree, rows, cols, coslat


def sample(lon, lat, times, path: Path = WAVES_FILE) -> pd.DataFrame:
    """Return wind_speed, wind_dir, wave_hs, wave_dir for each (lon, lat, time)."""
    ds, tree, rows, cols, coslat = _load(str(path))
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    t = pd.to_datetime(pd.Series(times), utc=True).dt.tz_convert(None).values.astype("datetime64[ns]")
    dist, idx = tree.query(np.column_stack([lon * coslat, lat]))
    near = dist <= _max_cell_distance_deg()
    tt = ds["time"].values
    ti = np.clip(np.searchsorted(tt, t), 0, len(tt) - 1)
    prev = np.clip(ti - 1, 0, len(tt) - 1)
    ti = np.where(np.abs(tt[prev] - t) < np.abs(tt[ti] - t), prev, ti)
    # a gap in the model archive must not lend a value from hours away
    near = near & (np.abs(tt[ti] - t) <= np.timedelta64(90, "m"))
    r, c = rows[idx], cols[idx]
    out = {}
    for name, var in (("wind_speed", "wind_speed"), ("wind_dir", "wind_dir"), ("wave_hs", "hs"),
                      ("wave_dir", "wave_dir")):
        v = ds[var].values[ti, r, c].astype(float)
        out[name] = np.where(near, v, np.nan)
    return pd.DataFrame(out)


def series_at(lon: float, lat: float, path: Path = WAVES_FILE, every_hours: int = 3) -> pd.DataFrame:
    ds, tree, rows, cols, coslat = _load(str(path))
    dist, idx = tree.query([lon * coslat, lat])
    r, c = rows[idx], cols[idx]
    sel = slice(None, None, every_hours)
    if dist > _max_cell_distance_deg():
        n = len(ds["time"].values[sel])
        return pd.DataFrame({"time": ds["time"].values[sel], "wind_speed": np.full(n, np.nan), "wave_hs": np.full(n, np.nan)})
    return pd.DataFrame({"time": ds["time"].values[sel], "wind_speed": ds["wind_speed"].values[sel, r, c],
                         "wave_hs": ds["hs"].values[sel, r, c]})
