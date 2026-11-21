"""Rules for AIS interpolation, detection matching, training labels and pass-based splits.

The numeric limits come from config/pipeline.yaml (section `matching`) and were frozen before any
evaluation result existed.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

KNOT = 0.514444  # m/s


def interpolate_track(track: pd.DataFrame, t: datetime, max_gap_s: float = 600.0,
                      max_extrapolate_s: float = 120.0) -> dict | None:
    """Position of one vessel at time t from its AIS points (columns t, x, y, sog, cog; x/y in meters).

    - Two points around t, each at most max_gap_s away: linear interpolation.
    - Otherwise the closest point, if at most max_extrapolate_s away: dead reckoning with its speed
      and course.
    - Otherwise None (the ship's position at t is not known well enough).
    """
    if track.empty:
        return None
    tr = track.sort_values("t")
    dt = (tr["t"] - t).dt.total_seconds().to_numpy()
    before = np.where(dt <= 0)[0]
    after = np.where(dt >= 0)[0]
    if len(before) and len(after):
        i, j = before[-1], after[0]
        if -dt[i] <= max_gap_s and dt[j] <= max_gap_s:
            w = 0.0 if i == j else -dt[i] / (dt[j] - dt[i])
            a, b = tr.iloc[i], tr.iloc[j]
            return {"x": a.x + w * (b.x - a.x), "y": a.y + w * (b.y - a.y),
                    "sog": float(np.nanmean([a.sog, b.sog])), "cog": float(a.cog if w < 0.5 else b.cog),
                    "gap_s": float(dt[j] - dt[i])}
    k = int(np.argmin(np.abs(dt)))
    if abs(dt[k]) > max_extrapolate_s:
        return None
    p = tr.iloc[k]
    sog = 0.0 if pd.isna(p.sog) else float(p.sog)
    cog = 0.0 if pd.isna(p.cog) else float(p.cog)
    move = sog * KNOT * (-dt[k])
    return {"x": p.x + move * np.sin(np.deg2rad(cog)), "y": p.y + move * np.cos(np.deg2rad(cog)),
            "sog": sog, "cog": cog, "gap_s": float(abs(dt[k]))}


def greedy_match(pairs: pd.DataFrame, max_distance_m: float) -> pd.DataFrame:
    """One-to-one matching: repeatedly take the closest remaining detection-ship pair within the limit."""
    p = pairs[pairs["distance_m"] <= max_distance_m].sort_values(["distance_m", "det_id", "vessel_hash"])
    used_d, used_v, out = set(), set(), []
    for row in p.itertuples(index=False):
        if row.det_id in used_d or row.vessel_hash in used_v:
            continue
        used_d.add(row.det_id)
        used_v.add(row.vessel_hash)
        out.append({"det_id": row.det_id, "vessel_hash": row.vessel_hash, "distance_m": row.distance_m})
    return pd.DataFrame(out, columns=["det_id", "vessel_hash", "distance_m"])


def label_detections(det: pd.DataFrame, matched_ids: set, exclusion_distance_m: float) -> pd.Series:
    """Training label per detection.

    ship      : matched to an AIS ship under the frozen matching rules
    not_ship  : no AIS ship within exclusion_distance_m (a likely false alarm, or a boat without AIS)
    ambiguous : unmatched but an AIS ship is near; excluded from training
    """
    near = det["nearest_ais_m"].fillna(np.inf)
    lab = np.where(det["det_id"].isin(matched_ids), "ship",
                   np.where(near > exclusion_distance_m, "not_ship", "ambiguous"))
    return pd.Series(lab, index=det.index)


def assign_splits(passes: pd.DataFrame, dev_fraction: float, ml_train_fraction: float,
                  ml_test_fraction: float) -> pd.DataFrame:
    """Split passes by acquisition time, never by detection.

    research_split: dev (first part, used to fix detector settings) | eval (rest)
    ml_split: train (first part) | calib (calibration and model selection) | test (last part)
    """
    p = passes.sort_values("start_time").reset_index(drop=True)
    n = len(p)
    n_dev = int(round(n * dev_fraction))
    n_train = int(round(n * ml_train_fraction))
    n_test = int(round(n * ml_test_fraction))
    p["research_split"] = ["dev" if i < n_dev else "eval" for i in range(n)]
    p["ml_split"] = ["train" if i < n_train else ("test" if i >= n - n_test else "calib") for i in range(n)]
    return p
