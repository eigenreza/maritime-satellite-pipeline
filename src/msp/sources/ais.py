"""AIS ship positions and static data from Kystdatahuset (Norwegian Coastal Administration, Kystverket).

The open endpoints used here need no login. Open access hides non-public ships (for example fishing
vessels under 15 m and leisure craft under 45 m), which matters when reading the share of radar
detections without an AIS match.

Privacy: MMSI, IMO, call signs and names are used only inside the pipeline. Every public file carries a
salted hash per vessel plus a ship type class and a length class (see `vessel_hash`, `type_class`,
`length_class`).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API = "https://kystdatahuset.kystverket.no/ws/api"
USER_AGENT = "msp-research-client"
POSITION_COLUMNS = ["mmsi", "time", "lon", "lat", "cog", "sog", "message_nr", "calc_speed",
                    "sec_prevpoint", "dist_prevpoint", "true_heading", "rot"]

LENGTH_CLASSES = [(0, 30, "under 30 m"), (30, 60, "30-60 m"), (60, 100, "60-100 m"),
                  (100, 200, "100-200 m"), (200, 10_000, "over 200 m")]


def _post(path: str, body: dict, retries: int = 3, timeout: int = 240) -> dict:
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(f"{API}{path}", json=body, timeout=timeout,
                              headers={"User-Agent": USER_AGENT, "accept": "application/json"})
            r.raise_for_status()
            data = r.json()
            if not data.get("success", False):
                raise RuntimeError(f"AIS service: {data.get('msg')}")
            return data
        except (requests.RequestException, RuntimeError) as exc:
            last = exc
            time.sleep(5 * attempt)
    raise RuntimeError(f"AIS request failed after {retries} attempts: {last}")


def _fmt(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y%m%d%H%M")


def fetch_positions(bbox: list[float], start: datetime, end: datetime, min_speed: float = 0.0) -> pd.DataFrame:
    """All AIS positions inside bbox between start and end (UTC)."""
    body = {"bbox": ",".join(f"{v:.6f}" for v in bbox), "start": _fmt(start), "end": _fmt(end),
            "minSpeed": min_speed}
    rows = _post("/ais/positions/within-bbox-time", body)["data"] or []
    df = pd.DataFrame(rows, columns=POSITION_COLUMNS[: len(rows[0])] if rows else POSITION_COLUMNS)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[["mmsi", "time", "lon", "lat", "cog", "sog", "true_heading"]].copy()
    df["mmsi"] = df["mmsi"].astype("int64")
    return df.sort_values(["mmsi", "time"]).reset_index(drop=True)


def fetch_positions_around(bbox: list[float], center: datetime, minutes: int) -> pd.DataFrame:
    return fetch_positions(bbox, center - timedelta(minutes=minutes), center + timedelta(minutes=minutes))


def fetch_static(mmsis: list[int], start: datetime, end: datetime, chunk: int = 200) -> pd.DataFrame:
    """Ship type and dimensions for the given MMSIs, valid between start and end."""
    frames = []
    for i in range(0, len(mmsis), chunk):
        params = [("start", start.strftime("%Y-%m-%dT%H:%M:%SZ")), ("end", end.strftime("%Y-%m-%dT%H:%M:%SZ")),
                  ("limit", "500000")] + [("mmsi", str(m)) for m in mmsis[i:i + chunk]]
        for attempt in range(1, 4):
            try:
                r = requests.get(f"{API}/ais/static-messages", params=params, timeout=240,
                                 headers={"User-Agent": USER_AGENT, "accept": "application/json"})
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(5 * attempt)
        body = r.json()
        rows = body.get("data", body) if isinstance(body, dict) else body
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame(columns=["mmsi", "ship_type", "length_m", "width_m"])
    st = pd.concat(frames, ignore_index=True)
    st["length_m"] = st["dimensionToBow"].fillna(0) + st["dimensionToStern"].fillna(0)
    st["width_m"] = st["dimensionToPort"].fillna(0) + st["dimensionToStarboard"].fillna(0)
    st = st.rename(columns={"shipType": "ship_type"})
    # One record per ship: the most recent version with a usable length, else the most recent one
    st["has_len"] = st["length_m"] > 0
    st = st.sort_values(["mmsi", "has_len", "validFrom"]).groupby("mmsi").tail(1)
    out = st[["mmsi", "ship_type", "length_m", "width_m"]].copy()
    out["mmsi"] = out["mmsi"].astype("int64")
    out.loc[out["length_m"] <= 0, "length_m"] = np.nan
    out.loc[out["width_m"] <= 0, "width_m"] = np.nan
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- privacy and classes

def load_salt(path: Path) -> str:
    """Secret salt for vessel hashes. Kept in data/work (never published); created on first use."""
    env = os.environ.get("MSP_HASH_SALT")
    if env:
        return env
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def vessel_hash(mmsi: int, salt: str) -> str:
    """Stable, non-reversible vessel id for public files: "v" followed by 11 hex characters."""
    h = hashlib.sha256(f"{salt}:{int(mmsi)}".encode()).hexdigest()
    return "v" + h[:11]


def type_class(code: float | int | None) -> str:
    """AIS ship type code to a plain class name."""
    if code is None or (isinstance(code, float) and np.isnan(code)):
        return "unknown"
    c = int(code)
    if c == 30:
        return "fishing"
    if c in (31, 32, 52):
        return "tug or towing"
    if c in (36, 37):
        return "sailing or leisure"
    if 40 <= c <= 49:
        return "high-speed craft"
    if c in (33, 34, 35, 50, 51, 53, 54, 55, 58, 59):
        return "service or government"
    if 60 <= c <= 69:
        return "passenger"
    if 70 <= c <= 79:
        return "cargo"
    if 80 <= c <= 89:
        return "tanker"
    return "other" if c > 0 else "unknown"


def length_class(length_m: float | None) -> str:
    if length_m is None or not np.isfinite(length_m) or length_m <= 0:
        return "unknown"
    for lo, hi, name in LENGTH_CLASSES:
        if lo <= length_m < hi:
            return name
    return "unknown"
