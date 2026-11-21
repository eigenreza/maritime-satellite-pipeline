"""Winter pass selection: chosen by sea state before any winter radar is downloaded.

Rule (frozen before processing, written to config/s1_passes_winter.txt and the decision log):
1. Candidates: every Sentinel-1 IW GRD scene from 1 Dec 2025 to 15 Mar 2026 covering at least 70% of
   the study box.
2. Sea state at the pass time, over the open sea part of the box (wave-model sea cells at least 3 km
   from land): median and maximum significant wave height and 10 m wind, from the same MET Norway wave
   model files used for summer.
3. Strata by the median values: stormy (waves >= 4 m or wind >= 15 m/s), moderate (waves 2 to 4 m),
   calm (the rest).
4. Selection, 18 passes: the 6 stormiest by median wave height (all, if fewer), 6 moderate spread evenly
   over their wave heights, and the rest calm spread evenly in time. Ties go to the earlier pass.
5. A pass is dropped and replaced by the next one from its stratum if open AIS has no positions in the
   box within 10 minutes of the pass, or if the wave archive has no model hour within 90 minutes of it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.ops import unary_union

from msp import paths
from msp.config import load_config

N_PASSES, N_STORMY, N_MODERATE = 18, 6, 6
OPEN_SEA_KM = 3.0


def open_sea_cells(ds: xr.Dataset, land_shp, crs: str) -> np.ndarray:
    """Boolean grid: wave-model sea cells in the box at least OPEN_SEA_KM from land."""
    cfg = load_config()
    w, s, e, n = cfg.area.bbox
    lat, lon = ds["lat"].values, ds["lon"].values
    sea = np.isfinite(ds["hs"].isel(time=0).values) & (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
    pts = gpd.GeoSeries(gpd.points_from_xy(lon[sea], lat[sea]), crs="EPSG:4326").to_crs(crs)
    land = unary_union(gpd.read_file(land_shp).to_crs(crs).geometry.values)
    far = np.array([p.distance(land) >= OPEN_SEA_KM * 1000 for p in pts])
    out = np.zeros_like(sea)
    out[np.nonzero(sea)] = far
    return out


def sea_state_at_passes(cands: list[dict], waves_file, land_shp) -> pd.DataFrame:
    cfg = load_config()
    ds = xr.load_dataset(waves_file)
    mask = open_sea_cells(ds, land_shp, cfg.area.crs)
    tt = pd.to_datetime(ds["time"].values)
    rows = []
    for c in cands:
        t = pd.Timestamp(c["start"]).tz_convert(None) if pd.Timestamp(c["start"]).tzinfo else pd.Timestamp(c["start"])
        k = int(np.argmin(np.abs(tt - t)))
        hs = ds["hs"].values[k][mask]
        ws = ds["wind_speed"].values[k][mask]
        rows.append({"pass_id": c["pass_id"], "start": c["start"], "platform": c["platform"],
                     "orbit": c["relative_orbit"], "direction": c["orbit_direction"],
                     "coverage": c["area_coverage"], "size_bytes": c["size_bytes"],
                     "hs_median": float(np.nanmedian(hs)), "hs_max": float(np.nanmax(hs)),
                     "wind_median": float(np.nanmedian(ws)), "wind_max": float(np.nanmax(ws)),
                     "model_hour_offset_min": round(abs((tt[k] - t).total_seconds()) / 60, 1)})
    df = pd.DataFrame(rows).sort_values("start").reset_index(drop=True)
    # a pass in a gap of the wave archive has no sea state: it cannot be placed in a stratum
    df.loc[df.model_hour_offset_min > 90, ["hs_median", "hs_max", "wind_median", "wind_max"]] = np.nan
    df["stratum"] = np.where(df.hs_median.isna(), "no sea state",
                             np.where((df.hs_median >= 4) | (df.wind_median >= 15), "stormy",
                                      np.where(df.hs_median >= 2, "moderate", "calm")))
    return df


def _even(df: pd.DataFrame, k: int, by: str) -> list[str]:
    """k rows spread evenly over the order of `by` (first and last included)."""
    if len(df) <= k:
        return list(df.pass_id)
    d = df.sort_values([by, "start"]).reset_index(drop=True)
    idx = np.unique(np.round(np.linspace(0, len(d) - 1, k)).astype(int))
    return list(d.loc[idx, "pass_id"])


def select(df: pd.DataFrame, has_ais) -> tuple[list[str], list[str]]:
    """Apply the rule; has_ais(pass_id) -> bool. Returns (selected, dropped for missing AIS)."""
    dropped = []
    keep = []
    for p in df.pass_id:
        if has_ais(p):
            keep.append(p)
        else:
            dropped.append(p)
    ok = df[df.pass_id.isin(keep)].copy()
    stormy = ok[ok.stratum == "stormy"].sort_values(["hs_median", "start"], ascending=[False, True])
    sel = list(stormy.pass_id[:N_STORMY])
    n_mod = N_MODERATE + max(0, N_STORMY - len(sel))
    sel += _even(ok[ok.stratum == "moderate"], n_mod, "hs_median")
    calm = ok[ok.stratum == "calm"].copy()
    calm["t"] = pd.to_datetime(calm.start)
    sel += _even(calm, N_PASSES - len(sel), "t")
    return sorted(sel, key=lambda p: df.set_index("pass_id").loc[p, "start"]), dropped


def ais_check(pass_id: str, start: str) -> bool:
    from msp.sources.ais import fetch_positions_around

    t = datetime.fromisoformat(start.replace("Z", "+00:00"))
    try:
        df = fetch_positions_around(list(load_config().area.bbox), t, 10)
    except Exception:
        return False
    return len(df) > 0


def write_pass_list(df: pd.DataFrame, selected: list[str], dropped: list[str], path) -> None:
    sel = df[df.pass_id.isin(selected)].sort_values("start")
    lines = [
        "# Winter passes (second held-out test set). Chosen by sea state before any winter radar was downloaded.",
        "# Rule: src/msp/pipeline/winter.py and docs/evaluation.md. Candidates: every IW GRD scene from",
        "# 1 Dec 2025 to 15 Mar 2026 covering at least 70% of the box; sea state over open sea (wave-model",
        "# cells at least 3 km from land) at the pass time; strata by median waves and wind; 6 stormiest,",
        "# 6 moderate spread over wave height, the rest calm spread over time.",
        f"# Candidates {len(df)}: stormy {int((df.stratum == 'stormy').sum())}, moderate "
        f"{int((df.stratum == 'moderate').sum())}, calm {int((df.stratum == 'calm').sum())}."
        + (f" Dropped for missing AIS: {', '.join(dropped)}." if dropped else " None dropped for missing AIS."),
        "# Format: pass id   # stratum, median and max waves (m), median and max wind (m/s)",
    ]
    for r in sel.itertuples():
        lines.append(f"{r.pass_id}   # {r.stratum}, waves {r.hs_median:.1f} / {r.hs_max:.1f}, "
                     f"wind {r.wind_median:.1f} / {r.wind_max:.1f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def candidates_json() -> list[dict]:
    """Search the catalogue for winter candidates and store them next to the summer ones."""
    import dataclasses

    from msp.sources.cdse import S1Scene, search_s1_grd

    cfg = load_config()
    end_next = (datetime.fromisoformat(cfg.winter.end) + timedelta(days=1)).strftime("%Y-%m-%d")
    sc = [s for s in search_s1_grd(tuple(cfg.area.bbox), cfg.winter.start, end_next)
          if s.start[:10] <= cfg.winter.end and s.area_coverage >= float(cfg.winter.min_area_coverage)]
    out = paths.WORK / "s1_candidates_winter.json"
    out.write_text(json.dumps([dataclasses.asdict(s) for s in sc], indent=1))
    return [dataclasses.asdict(s) | {"pass_id": S1Scene(**dataclasses.asdict(s)).pass_id} for s in sc]
