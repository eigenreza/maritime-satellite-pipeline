"""Training data for the ship classifier: one row per radar detection with label, features and cut-out.

Labels come for free from the AIS matching step (rules in docs/evaluation.md):
- ship      : matched to an AIS ship (length label = the AIS ship's length)
- not_ship  : no AIS ship within the exclusion distance
- ambiguous : in between; excluded from training and scoring
Unmatched does not always mean false alarm: small boats without AIS also end up as "not_ship".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from msp import db, paths
from msp.config import load_config
from msp.match.rules import label_detections
from msp.pipeline.ais_match import NEAREST_SQL
from msp.pipeline.s1 import pass_splits

FEATURES = ["n_pixels", "fill", "length_m", "width_m", "peak_vv_db", "mean_vv_db", "peak_vh_db", "bg_vv_db",
            "scr_db", "wind_speed", "wave_hs"]

DET_SQL = """
SELECT d.det_id, d.pass_id, d.det_idx, d.lon, d.lat, d.n_pixels, d.fill, d.length_m, d.width_m, d.peak_vv_db,
       d.mean_vv_db, d.peak_vh_db, d.bg_vv_db, d.scr_db, d.wind_speed, d.wave_hs,
       m.vessel_hash, m.distance_m, v.length_m AS ais_length_m, v.length_class AS ais_length_class,
       v.type_class AS ais_type_class
FROM detections d
LEFT JOIN matches m USING (det_id)
LEFT JOIN vessels v ON v.vessel_hash = m.vessel_hash
"""


def load_table() -> pd.DataFrame:
    cfg = load_config()
    det = db.query_df(DET_SQL)
    passes = db.query_df("SELECT pass_id, start_time FROM passes")
    near = pd.concat([db.query_df(NEAREST_SQL, {"pass_id": p}) for p in passes.pass_id], ignore_index=True)
    det = det.merge(near, on="det_id", how="left")
    det["label"] = label_detections(det, set(det.loc[det.vessel_hash.notna(), "det_id"]),
                                    float(cfg.matching.exclusion_distance_m)).values
    sp = passes.merge(pass_splits(), on="pass_id")
    det = det.merge(sp[["pass_id", "start_time", "ml_split", "research_split"]], on="pass_id")
    return det.sort_values(["start_time", "det_idx"]).reset_index(drop=True)


def load_chips(det: pd.DataFrame) -> np.ndarray:
    """Cut-outs in the same row order as `det` (2 x 64 x 64, sigma0 in dB)."""
    out = None
    for pid, g in det.groupby("pass_id", sort=False):
        z = np.load(paths.WORK / "chips" / f"{pid}.npz")
        chips, idx = z["chips"], z["det_idx"]
        pos = pd.Series(np.arange(len(idx)), index=idx)
        sel = chips[pos.loc[g.det_idx.values].values].astype(np.float32)
        if out is None:
            out = np.full((len(det),) + sel.shape[1:], np.nan, dtype=np.float32)
        out[det.index.get_indexer(g.index)] = sel
    return out


def normalize_chips(chips: np.ndarray) -> np.ndarray:
    """dB to a roughly unit range; missing pixels (outside the image) become the sea floor value."""
    x = (chips + 20.0) / 10.0
    return np.nan_to_num(x, nan=-1.0, posinf=3.0, neginf=-1.0).clip(-3, 5).astype(np.float32)
