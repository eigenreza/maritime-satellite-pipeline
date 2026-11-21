"""AIS around each radar pass, interpolation to the acquisition time, and matching in PostGIS.

The pass time is not one instant: a Sentinel-1 scene is acquired over about 25 seconds along the
orbit, so each ship gets the acquisition time at its own latitude (linear between the scene's first
and last line).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pyproj import Transformer

from msp import db, paths
from msp.config import load_config
from msp.match.rules import greedy_match, interpolate_track
from msp.sources import ais as ais_src

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
TO_LL = Transformer.from_crs("EPSG:32632", "EPSG:4326", always_xy=True)


def acquisition_time(lat: np.ndarray, start: datetime, stop: datetime, footprint_lat: tuple[float, float],
                     direction: str) -> np.ndarray:
    """Approximate acquisition time per latitude for a scene (descending passes fly north to south)."""
    lo, hi = footprint_lat
    frac = np.clip((np.asarray(lat) - lo) / max(hi - lo, 1e-6), 0, 1)
    if direction.upper().startswith("DESC"):
        frac = 1 - frac
    dur = (stop - start).total_seconds()
    return np.array([start + timedelta(seconds=float(f * dur)) for f in frac])


def ingest_ais_for_pass(p: dict, salt: str) -> dict:
    """Fetch positions around the pass, store hashed positions, interpolate each ship to its acquisition time."""
    cfg = load_config()
    bbox = cfg.area.bbox
    mid = p["start_time"] + (p["stop_time"] - p["start_time"]) / 2
    win = int(cfg.ais.window_minutes)
    pos = ais_src.fetch_positions(bbox, mid - timedelta(minutes=win), mid + timedelta(minutes=win))
    raw_dir = paths.RAW / "ais"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pos.to_parquet(raw_dir / f"{p['pass_id']}_positions.parquet")  # private (contains MMSI)
    if pos.empty:
        db.execute("DELETE FROM ais_positions WHERE pass_id = %s", (p["pass_id"],))
        db.execute("DELETE FROM ais_at_pass WHERE pass_id = %s", (p["pass_id"],))
        return {"positions": 0, "ships": 0}
    x, y = TO_UTM.transform(pos["lon"].values, pos["lat"].values)
    pos["x"], pos["y"] = x, y
    pos["vessel_hash"] = [ais_src.vessel_hash(m, salt) for m in pos["mmsi"]]
    fp = p["footprint"]
    _, lat0, _, lat1 = fp.bounds
    rows = []
    for (mmsi, vh), g in pos.groupby(["mmsi", "vessel_hash"]):
        t_acq = acquisition_time(np.array([g["lat"].median()]), p["start_time"], p["stop_time"], (lat0, lat1),
                                 p["orbit_direction"])[0]
        r = interpolate_track(g.rename(columns={"time": "t"}), t_acq,
                              max_gap_s=float(cfg.matching.max_time_offset_s), max_extrapolate_s=120.0)
        if r is None:
            continue
        lon, lat = TO_LL.transform(r["x"], r["y"])
        rows.append({"mmsi": mmsi, "vessel_hash": vh, "t_acq": t_acq, "x": r["x"], "y": r["y"], "lon": lon,
                     "lat": lat, "sog": r["sog"], "cog": r["cog"], "gap_s": r["gap_s"]})
    at = pd.DataFrame(rows)
    db.execute("DELETE FROM ais_positions WHERE pass_id = %s", (p["pass_id"],))
    db.execute("DELETE FROM ais_at_pass WHERE pass_id = %s", (p["pass_id"],))
    db.copy_rows("ais_positions", ["pass_id", "vessel_hash", "t", "geom", "sog", "cog"],
                 ((p["pass_id"], r.vessel_hash, r.time.isoformat(), f"POINT({r.x} {r.y})", r.sog, r.cog)
                  for r in pos.itertuples()), geom_cols={"geom": 32632})
    if not at.empty:
        db.copy_rows("ais_at_pass", ["pass_id", "vessel_hash", "t_acq", "geom", "lon", "lat", "sog", "cog", "gap_s"],
                     ((p["pass_id"], r.vessel_hash, r.t_acq.isoformat(), f"POINT({r.x} {r.y})", r.lon, r.lat,
                       r.sog, r.cog, r.gap_s) for r in at.itertuples()), geom_cols={"geom": 32632})
        # Ships the radar could have seen: inside the imaged sea area of this pass
        db.execute("""UPDATE ais_at_pass a SET in_scene = ST_Within(a.geom, p.valid_area)
                      FROM passes p WHERE p.pass_id = a.pass_id AND a.pass_id = %s""", (p["pass_id"],))
    return {"positions": int(len(pos)), "ships": int(pos["mmsi"].nunique()), "interpolated": int(len(at)),
            "mmsi": sorted(pos["mmsi"].unique().tolist())}


def store_vessels(static: pd.DataFrame, salt: str) -> int:
    if static.empty:
        return 0
    v = pd.DataFrame({
        "vessel_hash": [ais_src.vessel_hash(m, salt) for m in static["mmsi"]],
        "type_class": static["ship_type"].map(ais_src.type_class),
        "length_m": static["length_m"],
        "length_class": static["length_m"].map(ais_src.length_class),
    }).drop_duplicates("vessel_hash")
    db.execute("DELETE FROM vessels WHERE vessel_hash = ANY(%s)", (list(v["vessel_hash"]),))
    return db.copy_rows("vessels", list(v.columns), v.itertuples(index=False, name=None))


CANDIDATES_SQL = """
-- Candidate detection-ship pairs: every AIS ship within the match distance of a detection (same pass)
SELECT d.det_id, a.vessel_hash, ST_Distance(d.geom, a.geom) AS distance_m
FROM detections d
JOIN ais_at_pass a ON a.pass_id = d.pass_id AND ST_DWithin(d.geom, a.geom, %(dist)s)
WHERE d.pass_id = %(pass_id)s
"""

NEAREST_SQL = """
-- Distance from each detection to the nearest AIS ship of the same pass (k-nearest-neighbour index scan)
SELECT d.det_id, n.distance_m AS nearest_ais_m
FROM detections d
LEFT JOIN LATERAL (
    SELECT ST_Distance(d.geom, a.geom) AS distance_m
    FROM ais_at_pass a WHERE a.pass_id = d.pass_id
    ORDER BY d.geom <-> a.geom LIMIT 1
) n ON true
WHERE d.pass_id = %(pass_id)s
"""


def match_pass(pass_id: str) -> dict:
    cfg = load_config()
    dist = float(cfg.matching.max_distance_m)
    pairs = db.query_df(CANDIDATES_SQL, {"dist": dist, "pass_id": pass_id})
    m = greedy_match(pairs, dist) if not pairs.empty else pd.DataFrame(columns=["det_id", "vessel_hash", "distance_m"])
    db.execute("DELETE FROM matches WHERE pass_id = %s", (pass_id,))
    if not m.empty:
        db.copy_rows("matches", ["pass_id", "det_id", "vessel_hash", "distance_m"],
                     ((pass_id, int(r.det_id), r.vessel_hash, float(r.distance_m)) for r in m.itertuples()))
    return {"candidates": int(len(pairs)), "matched": int(len(m))}
