"""Sea state at points: missing stays missing, never 0 (a point far from every model sea cell)."""

import numpy as np
import pandas as pd
import xarray as xr

from msp.metocean.sample import sample


def _grid(tmp_path):
    lat, lon = np.meshgrid(np.linspace(60.50, 60.60, 11), np.linspace(4.50, 4.70, 11), indexing="ij")
    hs = np.full((2, 11, 11), 1.5, dtype="float32")
    hs[:, :, 6:] = np.nan  # the eastern half is land in the wave model
    ws = np.full((2, 11, 11), 8.0, dtype="float32")
    ws[:, :, 6:] = np.nan
    ds = xr.Dataset(
        {"hs": (("time", "rlat", "rlon"), hs), "wind_speed": (("time", "rlat", "rlon"), ws),
         "wind_dir": (("time", "rlat", "rlon"), np.zeros_like(ws)), "wave_dir": (("time", "rlat", "rlon"), np.zeros_like(hs))},
        coords={"time": pd.to_datetime(["2026-08-10T06:00", "2026-08-10T07:00"]), "lat": (("rlat", "rlon"), lat),
                "lon": (("rlat", "rlon"), lon)})
    p = tmp_path / "waves.nc"
    ds.to_netcdf(p)
    return p


def test_sea_point_gets_the_model_value(tmp_path):
    p = _grid(tmp_path)
    s = sample([4.52], [60.55], [pd.Timestamp("2026-08-10T06:10Z")], path=p)
    assert s.wave_hs.iloc[0] == 1.5 and s.wind_speed.iloc[0] == 8.0


def test_point_far_from_any_sea_cell_is_missing_not_zero(tmp_path):
    p = _grid(tmp_path)
    # 5 km east of the last sea cell (inland), and a point outside the grid altogether
    s = sample([4.70 + 5000 / 111_320 / np.cos(np.deg2rad(60.55)), 3.0], [60.55, 59.0],
               [pd.Timestamp("2026-08-10T06:10Z")] * 2, path=p)
    assert s.wave_hs.isna().all() and s.wind_speed.isna().all()
    assert not (s.wave_hs == 0).any()
