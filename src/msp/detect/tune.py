"""Fix the detector's second-stage thresholds on the development passes only.

For every combination of minimum contrast (signal-to-clutter ratio, dB) and minimum peak brightness (dB),
the stored CFAR candidates of the development passes are filtered and matched to the AIS ships with the
frozen matching rules. The combination with the best F1 score (balance of AIS ships found and detections
explained by AIS) is written to the config and then frozen.
"""

from __future__ import annotations

import re

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from msp import db, paths
from msp.config import ROOT, load_config
from msp.match.rules import greedy_match
from msp.pipeline.s1 import candidates_path, pass_splits

SCR_GRID = [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 27.0, 30.0]
PEAK_GRID = [-99.0, -18.0, -15.0, -12.0, -10.0, -8.0, -6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0, 8.0, 10.0]


def _match_count(det: pd.DataFrame, ais: pd.DataFrame, max_d: float) -> int:
    if det.empty or ais.empty:
        return 0
    tree = cKDTree(ais[["x", "y"]].to_numpy())
    pairs = tree.query_ball_point(det[["x", "y"]].to_numpy(), r=max_d)
    rows = []
    for i, js in enumerate(pairs):
        for j in js:
            d = float(np.hypot(det.x.iloc[i] - ais.x.iloc[j], det.y.iloc[i] - ais.y.iloc[j]))
            rows.append((i, ais.vessel_hash.iloc[j], d))
    if not rows:
        return 0
    p = pd.DataFrame(rows, columns=["det_id", "vessel_hash", "distance_m"])
    return len(greedy_match(p, max_d))


def run() -> dict:
    cfg = load_config()
    max_d = float(cfg.matching.max_distance_m)
    sp = pass_splits()
    dev = sp.loc[sp.research_split == "dev", "pass_id"].tolist()
    done = set(db.query_df("SELECT pass_id FROM passes").pass_id)
    if missing := [p for p in dev if p not in done]:
        raise RuntimeError(f"development passes not processed yet: {missing}")
    from msp.flows import refresh_visibility

    data = []
    for pid in dev:
        refresh_visibility(pid)
        cand = gpd.read_parquet(candidates_path(pid))
        ais = db.query_df("""SELECT vessel_hash, ST_X(geom) x, ST_Y(geom) y FROM ais_at_pass
                             WHERE pass_id = %s AND in_scene""", (pid,))
        data.append((pid, cand, ais))
    rows = []
    for scr in SCR_GRID:
        for peak in PEAK_GRID:
            n_det = n_match = n_ais = 0
            for _, cand, ais in data:
                d = cand[(cand.scr_db >= scr) & (cand.peak_vv_db >= peak)].reset_index(drop=True)
                n_det += len(d)
                n_ais += len(ais)
                n_match += _match_count(d, ais, max_d)
            recall = n_match / max(n_ais, 1)
            precision = n_match / max(n_det, 1)
            f1 = 2 * precision * recall / max(precision + recall, 1e-9)
            rows.append({"min_scr_db": scr, "min_peak_db": peak, "detections": n_det, "matched": n_match,
                         "ais_visible": n_ais, "recall": round(recall, 4), "precision": round(precision, 4),
                         "f1": round(f1, 4)})
    tab = pd.DataFrame(rows).sort_values("f1", ascending=False)
    paths.RESULTS.mkdir(parents=True, exist_ok=True)
    tab.to_csv(paths.RESULTS / "detector_tuning_dev.csv", index=False)
    best = tab.iloc[0]
    _write_config(float(best.min_scr_db), float(best.min_peak_db))
    return {"dev_passes": len(dev), **best.to_dict()}


def _write_config(scr: float, peak: float) -> None:
    p = ROOT / "config" / "pipeline.yaml"
    t = p.read_text(encoding="utf-8")
    block = (f"  # second stage, fixed on the development passes by `msp tune-detector` (frozen)\n"
             f"  min_scr_db: {scr}\n  min_peak_db: {peak}\n")
    t = re.sub(r"  # second stage, fixed on the development passes.*\n  min_scr_db: .*\n  min_peak_db: .*\n", "", t)
    t = t.replace("  merge_distance_px: 3\n", "  merge_distance_px: 3\n" + block, 1)
    p.write_text(t, encoding="utf-8")
    load_config.cache_clear()
