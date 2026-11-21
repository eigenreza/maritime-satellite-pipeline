"""demo_export: everything the website shows, precomputed into web/public/data.

Images are warped to Web Mercator (EPSG:3857) so that MapLibre can place them exactly with four corner
coordinates. No vessel identity is exported: AIS features carry only a type class and a length class.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds

from msp import db, paths
from msp.config import load_config
from msp.metocean.sample import WAVES_FILE, sample
from msp.pipeline.s1 import pass_splits

OUT = paths.DEMO_DATA
TO_LL = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _clean(o):
    """NaN and missing values become null; numpy scalars become plain numbers (valid JSON)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if o is pd.NA or o is pd.NaT:
        return None
    return o


def _write_json(name: str, obj) -> None:
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_clean(obj), separators=(",", ":"), default=_default, allow_nan=False), encoding="utf-8")


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else round(float(o), 4)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.strftime("%Y-%m-%dT%H:%M:%SZ")
    raise TypeError(type(o))


def _r(x, d=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), d)


def _webp(rgba: np.ndarray, path: Path, quality: int = 82) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="WEBP", quality=quality, method=5)
    path.write_bytes(buf.getvalue())


def _to_3857(src_path: Path, res: float, band_count: int | None = None, resampling=Resampling.average,
             src_nodata=None) -> tuple[np.ndarray, list]:
    with rasterio.open(src_path) as src:
        dst_crs = "EPSG:3857"
        tr, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds, resolution=res)
        n = band_count or src.count
        out = np.full((n, h, w), np.nan, dtype=np.float32)
        for b in range(n):
            reproject(rasterio.band(src, b + 1), out[b], src_transform=src.transform, src_crs=src.crs,
                      dst_transform=tr, dst_crs=dst_crs, resampling=resampling,
                      src_nodata=src.nodata if src_nodata is None else src_nodata, dst_nodata=np.nan)
    left, top = tr.c, tr.f
    right, bottom = left + tr.a * w, top + tr.e * h
    corners = [TO_LL.transform(x, y) for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))]
    return out, [[round(a, 6), round(b, 6)] for a, b in corners]


def _corners_for(bounds_3857) -> list:
    left, bottom, right, top = bounds_3857
    return [[round(a, 6), round(b, 6)] for a, b in (TO_LL.transform(x, y) for x, y in
                                                       ((left, top), (right, top), (right, bottom), (left, bottom)))]


WAVE_STOPS = [(0.0, (208, 240, 255)), (1.0, (95, 179, 232)), (2.0, (47, 111, 191)), (3.0, (106, 61, 154)),
              (4.5, (194, 24, 91))]


def _colormap(v: np.ndarray, stops) -> np.ndarray:
    xs = np.array([s[0] for s in stops])
    rgb = np.zeros(v.shape + (4,), dtype=np.uint8)
    for c in range(3):
        rgb[..., c] = np.interp(np.nan_to_num(v, nan=0.0), xs, [s[1][c] for s in stops]).astype(np.uint8)
    rgb[..., 3] = np.where(np.isfinite(v), 255, 0)
    return rgb


SST_STOPS = [(8.0, (49, 54, 149)), (10.0, (69, 117, 180)), (12.0, (116, 173, 209)), (13.0, (254, 224, 144)),
             (14.5, (244, 109, 67)), (16.0, (165, 0, 38))]


# ------------------------------------------------------------------ pieces

def export_land(tolerance_m: float = 25.0) -> dict:
    land = gpd.read_file(paths.PRODUCTS / "landmask" / "land.shp")
    land["geometry"] = land.geometry.simplify(tolerance_m, preserve_topology=True)
    land = land[land.area > 2500].to_crs("EPSG:4326")
    gj = json.loads(land[["geometry"]].to_json(drop_id=True))
    for f in gj["features"]:
        f["properties"] = {}
    _round_coords(gj, 5)
    _write_json("land.geojson", gj)
    return {"features": len(gj["features"])}


def _round_coords(obj, d):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "coordinates":
                obj[k] = _round_list(v, d)
            else:
                _round_coords(v, d)
    elif isinstance(obj, list):
        for v in obj:
            _round_coords(v, d)


def _round_list(v, d):
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], (int, float)):
        return [round(float(x), d) for x in v]
    return [_round_list(x, d) for x in v]


def export_radar(pass_id: str, res: float = 100.0) -> list:
    from msp.pipeline.s1 import product_cog

    db_vals, corners = _to_3857(product_cog(pass_id), res, 1, Resampling.average, src_nodata=-32768)
    v = db_vals[0] / 100.0
    g = np.clip((v + 25.0) / 25.0 * 255.0, 0, 255)
    rgba = np.zeros(v.shape + (4,), dtype=np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = np.nan_to_num(g).astype(np.uint8)
    rgba[..., 3] = np.where(np.isfinite(v), 255, 0)
    _webp(rgba, OUT / "radar" / f"{pass_id}.webp", quality=75)
    # a light copy at half the resolution, drawn first on the map; the full image replaces it once loaded
    h, w = rgba.shape[:2]
    lite = Image.fromarray(rgba, mode="RGBA").resize((max(1, w // 2), max(1, h // 2)), Image.Resampling.BOX)
    _webp(np.asarray(lite), OUT / "radar" / f"{pass_id}_lite.webp", quality=70)
    return corners


_GRID_CACHE: dict = {}


def _wave_grid(corners_shape_key, bounds_3857, shape):
    """Index from a regular Web Mercator grid to the nearest wave-model sea cell."""
    key = (tuple(np.round(bounds_3857, 1)), shape)
    if key in _GRID_CACHE:
        return _GRID_CACHE[key]
    from scipy.spatial import cKDTree

    ds = xr.open_dataset(WAVES_FILE)
    lat, lon = ds["lat"].values, ds["lon"].values
    sea = np.isfinite(ds["hs"].isel(time=0).values)
    coslat = np.cos(np.deg2rad(60.6))
    tree = cKDTree(np.column_stack([lon[sea] * coslat, lat[sea]]))
    rows, cols = np.nonzero(sea)
    left, bottom, right, top = bounds_3857
    h, w = shape
    xs = left + (np.arange(w) + 0.5) * (right - left) / w
    ys = top - (np.arange(h) + 0.5) * (top - bottom) / h
    gx, gy = np.meshgrid(xs, ys)
    glon, glat = TO_LL.transform(gx, gy)
    d, idx = tree.query(np.column_stack([glon.ravel() * coslat, glat.ravel()]))
    ok = d < 0.012
    _GRID_CACHE[key] = (rows[idx], cols[idx], ok.reshape(shape), ds)
    return _GRID_CACHE[key]


def export_waves(pass_id: str, t: datetime, bbox: list[float]) -> str:
    from pyproj import Transformer as T

    to3857 = T.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x0, y0 = to3857.transform(bbox[0], bbox[1])
    x1, y1 = to3857.transform(bbox[2], bbox[3])
    shape = (int((y1 - y0) / 800), int((x1 - x0) / 800))
    r, c, ok, ds = _wave_grid(None, (x0, y0, x1, y1), shape)
    ti = int(np.argmin(np.abs(ds["time"].values - np.datetime64(t.replace(tzinfo=None), "ns"))))
    hs = ds["hs"].values[ti][r, c].reshape(shape)
    hs = np.where(ok, hs, np.nan)
    _webp(_colormap(hs, WAVE_STOPS), OUT / "waves" / f"{pass_id}.webp", quality=75)
    return json.dumps(_corners_for((x0, y0, x1, y1)))


def export_wind(pass_id: str, t: datetime, bbox: list[float], step: float = 0.07) -> str:
    lons = np.arange(bbox[0] + step / 2, bbox[2], step)
    lats = np.arange(bbox[1] + step / 2, bbox[3], step / 2)
    gx, gy = np.meshgrid(lons, lats)
    s = sample(gx.ravel(), gy.ravel(), [t] * gx.size)
    land = gpd.read_file(paths.PRODUCTS / "landmask" / "land.shp").to_crs("EPSG:4326")
    pts = gpd.GeoDataFrame(s, geometry=gpd.points_from_xy(gx.ravel(), gy.ravel()), crs="EPSG:4326")
    on_land = gpd.sjoin(pts, land[["geometry"]], predicate="within", how="left").index_right.notna()
    on_land = on_land.groupby(level=0).max()
    feats = []
    for _i, row in pts[~on_land.values].iterrows():
        if not np.isfinite(row.wind_speed):
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(row.geometry.x, 4), round(row.geometry.y, 4)]},
                      "properties": {"speed": _r(row.wind_speed, 1), "dir": _r(row.wind_dir, 0)}})
    _write_json(f"wind/{pass_id}.geojson", {"type": "FeatureCollection", "features": feats})
    return f"wind/{pass_id}.geojson"


DETS_SQL = """
SELECT d.det_id, d.det_idx, d.lon, d.lat, d.length_m, d.width_m, d.peak_vv_db, d.scr_db, d.wind_speed, d.wave_hs,
       d.ship_prob, d.est_length_m, m.distance_m, v.type_class, v.length_class
FROM detections d
LEFT JOIN matches m USING (det_id)
LEFT JOIN vessels v ON v.vessel_hash = m.vessel_hash
WHERE d.pass_id = %s
"""

AIS_SQL = """
SELECT a.lon, a.lat, a.sog, a.wind_speed, a.wave_hs, v.type_class, v.length_class,
       (m.det_id IS NOT NULL) AS detected
FROM ais_at_pass a
LEFT JOIN vessels v USING (vessel_hash)
LEFT JOIN matches m ON m.pass_id = a.pass_id AND m.vessel_hash = a.vessel_hash
WHERE a.pass_id = %s AND a.in_scene
"""


def _line_ratio() -> float | None:
    v = load_config().detector.get("line_max_elongation")
    return float(v) if v is not None else None


def export_points(pass_id: str) -> dict:
    from msp.detect.shape import is_line_shaped

    d = db.query_df(DETS_SQL, (pass_id,))
    ratio = _line_ratio()
    d["line"] = is_line_shaped(d.length_m, d.width_m, ratio) if ratio is not None and len(d) else False
    feats = []
    for r in d.itertuples():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.lon, 5), round(r.lat, 5)]},
                      "properties": {"matched": bool(pd.notna(r.distance_m)), "ai": _r(r.ship_prob, 3),
                                     "ai_len": _r(r.est_length_m, 0), "peak_db": _r(r.peak_vv_db, 1),
                                     "scr_db": _r(r.scr_db, 1), "len": _r(r.length_m, 0),
                                     "dist": _r(r.distance_m, 0), "type": r.type_class if isinstance(r.type_class, str) else None,
                                     "length_class": r.length_class if isinstance(r.length_class, str) else None,
                                     "wind": _r(r.wind_speed, 1), "waves": _r(r.wave_hs, 2),
                                     "width": _r(r.width_m, 0), "line": bool(r.line)}})
    _write_json(f"detections/{pass_id}.geojson", {"type": "FeatureCollection", "features": feats})
    a = db.query_df(AIS_SQL, (pass_id,))
    afeats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.lon, 5), round(r.lat, 5)]},
               "properties": {"type": r.type_class or "unknown", "length_class": r.length_class or "unknown",
                              "sog": _r(r.sog, 1), "detected": bool(r.detected), "wind": _r(r.wind_speed, 1),
                              "waves": _r(r.wave_hs, 2)}} for r in a.itertuples()]
    _write_json(f"ais/{pass_id}.geojson", {"type": "FeatureCollection", "features": afeats})
    return {"detections": len(d), "matched": int(d.distance_m.notna().sum()), "line_shaped": int(d["line"].sum()),
            "ai_removed": int((d.ship_prob < 0.5).sum()) if d.ship_prob.notna().any() else 0,
            "ais_visible": len(a), "ais_detected": int(a.detected.sum()),
            "wind_mean": float(np.nanmean(a.wind_speed)) if len(a) else float("nan"),
            "wave_mean": float(np.nanmean(a.wave_hs)) if len(a) else float("nan")}


def export_s2() -> list[dict]:
    rows = [json.loads(x) for x in (paths.WORK / "s2_cloud.jsonl").read_text().splitlines() if x.strip()]
    df = pd.DataFrame(rows).drop_duplicates("day", keep="last").sort_values("day")
    out = []
    for r in df.itertuples():
        d = r.day.replace("-", "")
        cm = paths.PRODUCTS / "s2" / f"S2_{d}_cloudmask.tif"
        if not cm.exists():
            continue
        arr, corners = _to_3857(cm, 200.0, 1, Resampling.nearest)
        a = arr[0]
        rgba = np.zeros(a.shape + (4,), dtype=np.uint8)
        rgba[..., :3] = 255
        rgba[..., 3] = np.where(a == 1, 200, 0)
        _webp(rgba, OUT / "s2" / f"{r.day}_cloud.webp", quality=70)
        tc = paths.PRODUCTS / "s2" / f"S2_{d}_truecolor.tif"
        tci, tci_corners = None, None
        if tc.exists():
            rgb, corners_tc = _to_3857(tc, 120.0, 3, Resampling.average, src_nodata=0)
            img = np.zeros(rgb.shape[1:] + (4,), dtype=np.uint8)
            for c in range(3):
                img[..., c] = np.clip(np.nan_to_num(rgb[c]) * 1.15, 0, 255).astype(np.uint8)
            img[..., 3] = np.where(np.isfinite(rgb[0]) & (np.nan_to_num(rgb).sum(0) > 0), 255, 0)
            _webp(img, OUT / "s2" / f"{r.day}.webp", quality=80)
            tci = f"s2/{r.day}.webp"
            tci_corners = corners_tc
        out.append({"day": r.day, "time": r.time, "cloud_share": _r(r.cloud_share, 3),
                    "observed": _r(r.sea_observed_share, 3), "truecolor": tci,
                    "cloudmask": f"s2/{r.day}_cloud.webp", "bounds": corners, "truecolor_bounds": tci_corners})
    _write_json("s2.json", out)
    return out


def export_sst() -> list[dict]:
    out = []
    for nc in sorted((paths.PRODUCTS / "s3").glob("S3_SST_*.nc")):
        d = nc.stem.split("_")[-1]
        day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        tif = paths.PRODUCTS / "s3" / f"S3_SST_{d}_celsius.tif"
        arr, corners = _to_3857(tif, 600.0, 1, Resampling.nearest, src_nodata=-999.0)
        v = arr[0]
        valid = float(np.isfinite(v).mean())
        img = None
        if np.isfinite(v).sum() > 50:
            _webp(_colormap(v, SST_STOPS), OUT / "sst" / f"{day}.webp", quality=80)
            img = f"sst/{day}.webp"
        with xr.open_dataset(nc) as ds:
            t = pd.Timestamp(ds["time"].values[0]).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({"day": day, "time": t, "image": img, "valid_share": round(valid, 4), "bounds": corners,
                    "min": _r(np.nanmin(v), 1) if img else None, "max": _r(np.nanmax(v), 1) if img else None})
    _write_json("sst.json", out)
    return out


def export_grid_series(bbox: list[float], step: float = 0.1, every_hours: int = 3) -> None:
    """Wind, waves and sea temperature at a coarse grid of sea points, one series per season."""
    cfg = load_config()
    out = {}
    for name, sec in (("summer", cfg.period), ("winter", cfg.winter)):
        out[name] = _grid_series(bbox, sec.start, sec.end, step, every_hours, sst=name == "summer")
    _write_json("grid_series.json", out)


def _grid_series(bbox: list[float], start: str, end: str, step: float, every_hours: int, sst: bool) -> dict:
    lons = np.round(np.arange(bbox[0] + step / 2, bbox[2], step), 3)
    lats = np.round(np.arange(bbox[1] + step / 2, bbox[3], step), 3)
    gx, gy = np.meshgrid(lons, lats)
    ds = xr.open_dataset(WAVES_FILE).sel(time=slice(f"{start}T00:00", f"{end}T23:59"))
    times = pd.to_datetime(ds["time"].values[::every_hours])
    from scipy.spatial import cKDTree

    lat, lon = ds["lat"].values, ds["lon"].values
    sea = np.isfinite(ds["hs"].isel(time=0).values)
    coslat = np.cos(np.deg2rad(60.6))
    tree = cKDTree(np.column_stack([lon[sea] * coslat, lat[sea]]))
    rows, cols = np.nonzero(sea)
    dist, idx = tree.query(np.column_stack([gx.ravel() * coslat, gy.ravel()]))
    wind = ds["wind_speed"].values[::every_hours][:, rows[idx], cols[idx]]
    waves = ds["hs"].values[::every_hours][:, rows[idx], cols[idx]]
    far = dist > 0.03
    wind[:, far] = np.nan
    waves[:, far] = np.nan
    sst_days, sst_vals = [], []
    for nc in sorted((paths.PRODUCTS / "s3").glob("S3_SST_*.nc")) if sst else []:
        with xr.open_dataset(nc) as s:
            v = s["sst"].isel(time=0).sel(lat=xr.DataArray(gy.ravel()), lon=xr.DataArray(gx.ravel()), method="nearest").values - 273.15
        if np.isfinite(v).sum() == 0:
            continue
        d = nc.stem.split("_")[-1]
        sst_days.append(f"{d[:4]}-{d[4:6]}-{d[6:]}")
        sst_vals.append(v)
    sst_arr = np.array(sst_vals).T if sst_vals else np.zeros((gx.size, 0))

    def q(a, d=1):
        return [[None if not np.isfinite(x) else round(float(x), d) for x in row] for row in a]

    return {"lons": lons.tolist(), "lats": lats.tolist(), "times": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
            "wind": q(wind.T), "waves": q(waves.T, 2), "sst_days": sst_days, "sst": q(sst_arr)}



def _game_note(is_ship: bool, difficulty: str, length_m, wind, waves, p_cnn: float, line: bool = False,
               elongation: float | None = None, fill: float | None = None, scr_db: float | None = None) -> str:
    """One sentence on why a round is easy or hard, from that detection's measured shape, brightness and
    sea state and from the truth, so it never contradicts the cut-out."""
    rough = (wind or 0) >= 10 or (waves or 0) >= 1.5
    if line:
        if is_ship:
            return "A long straight bright streak, yet an AIS ship is there: a ship with a long wake or a structure beside it."
        return "A long straight bright line: more likely a bridge, cable or structure than a boat."
    compact = (elongation or 0) <= 4 and (fill or 0) >= 0.35
    if is_ship and difficulty == "easy":
        return ("A big, bright, compact echo: a clear ship." if compact else
                "A big, bright echo, stretched along the ship's length: a clear ship.")
    if is_ship:
        if rough and (length_m or 0) >= 100:
            return "A big ship in rough water: its echo still stands out, though the sea around it is bright and grainy."
        if rough:
            return "A real ship in rough water: the bright, grainy sea around it hides much of its echo."
        if (length_m or 0) < 30:
            return "A real ship, but a small one: its echo is barely brighter than the sea around it."
        return "A real ship whose echo is weak or smeared here, so it hardly stands out from the sea."
    if p_cnn >= 0.3:
        if rough:
            return "No AIS ship within 1.5 km: likely a bright patch of rough sea, or a boat without AIS."
        return ("No AIS ship within 1.5 km, yet a bright compact echo in calm water: likely a boat without AIS."
                if compact else "No AIS ship within 1.5 km: a bright but irregular echo, perhaps a boat without AIS or a small structure.")
    return "A weak, scattered echo with no ship shape: sea clutter." if (scr_db or 99) < 20 else \
        "No AIS ship nearby and an irregular shape: sea clutter or a small fixed object."


def export_game(n_easy: int = 30, n_hard: int = 30, seed: int = 11) -> int:
    """Rounds for 'You versus the AI', drawn from the test passes only."""
    import matplotlib

    matplotlib.use("Agg")
    from msp.ml.data import load_chips, load_table

    pred = pd.read_parquet(paths.WORK / "ml_predictions.parquet")
    t = load_table().merge(pred[["det_id", "p_cnn", "len_cnn"]], on="det_id")
    t = t[t.ml_split.isin(["test", "winter"]) & (t.label != "ambiguous")].reset_index(drop=True)
    from msp.detect.shape import elongation, is_line_shaped

    ratio = _line_ratio()
    t["elong"] = elongation(t.length_m, t.width_m)
    t["line"] = is_line_shaped(t.length_m, t.width_m, ratio) if ratio is not None else False
    rng = np.random.default_rng(seed)
    ship = t.label == "ship"
    easy = t[(ship & (t.ais_length_m >= 60) & (t.wave_hs < 1.5) & ~t.line) | (~ship & (t.scr_db < 22) & (t.p_cnn < 0.2))]
    hard = t[(ship & ((t.ais_length_m < 30) | (t.wave_hs >= 2.0))) | (~ship & (t.p_cnn >= 0.3))]
    # half the rounds from each season's test set, where it has enough cases
    parts = []
    for k, (frame, label) in enumerate(((easy, "easy"), (hard, "hard"))):
        n = n_easy if label == "easy" else n_hard
        for j, season in enumerate(("test", "winter")):
            f = frame[frame.ml_split == season]
            parts.append(f.sample(min(n // 2, len(f)), random_state=seed + 2 * k + j).assign(difficulty=label))
    pick = pd.concat(parts)
    pick = pick.drop_duplicates("det_id")
    chips = load_chips(pick.reset_index(drop=True))
    out = []
    gdir = OUT / "game"
    if gdir.exists():
        shutil.rmtree(gdir)
    gdir.mkdir(parents=True)
    for k, (r, chip) in enumerate(zip(pick.itertuples(), chips, strict=True)):
        img = np.nan_to_num(chip[0], nan=-30.0)
        g = np.clip((img + 25.0) / 30.0 * 255, 0, 255).astype(np.uint8)
        Image.fromarray(g, mode="L").save(gdir / f"r{k:03d}.png", optimize=True)
        is_ship = r.label == "ship"
        note = _game_note(is_ship, r.difficulty, r.ais_length_m, r.wind_speed, r.wave_hs, r.p_cnn, bool(r.line),
                          float(r.elong), float(r.fill), float(r.scr_db))
        out.append({"image": f"game/r{k:03d}.png", "wind": _r(r.wind_speed, 1), "waves": _r(r.wave_hs, 2),
                    "truth": "ship" if is_ship else "not a ship",
                    "length_m": _r(r.ais_length_m, 0) if is_ship else None,
                    "length_class": r.ais_length_class if is_ship and isinstance(r.ais_length_class, str) else None,
                    "ai_prob": _r(r.p_cnn, 3), "ai_len": _r(r.len_cnn, 0), "difficulty": r.difficulty, "note": note,
                    "line": bool(r.line), "season": "winter" if r.ml_split == "winter" else "summer"})
    rng.shuffle(out)
    _write_json("game.json", out)
    return len(out)


def export_clouds(passes: list[dict], s2: list[dict], sst: list[dict], start: str, end: str) -> None:
    d0, d1 = datetime.fromisoformat(start), datetime.fromisoformat(end)
    s2m = {x["day"]: x for x in s2}
    sstm = {x["day"]: x for x in sst}
    days = []
    while d0 <= d1:
        k = d0.strftime("%Y-%m-%d")
        s = s2m.get(k)
        days.append({"day": k, "s2_cloud": s["cloud_share"] if s and (s["observed"] or 0) >= 0.2 else None,
                     "s2_observed": s["observed"] if s else None,
                     "radar": sum(1 for p in passes if p["time"][:10] == k),
                     "sst_valid": sstm[k]["valid_share"] if k in sstm else None,
                     "truecolor": s["truecolor"] if s else None})
        d0 += timedelta(days=1)
    _write_json("clouds.json", days)


FLOW_LABELS = {"s1_ingest": "Sentinel-1 ingest", "ship_detect": "Ship detection", "ais_ingest": "AIS ingest",
               "metocean_ingest": "Sea state at points", "match": "Matching", "s2_ingest": "Sentinel-2 ingest",
               "s3_ingest": "Sentinel-3 ingest", "ecmwf_ingest": "ECMWF forecasts", "ship_classify": "AI classifier",
               "catalogue": "Catalogue"}
SOURCE_LABELS = {"sentinel-1": "Sentinel-1 radar", "sentinel-2": "Sentinel-2 optical", "sentinel-3": "Sentinel-3 SST",
                 "ecmwf-forecast": "ECMWF forecast", "ais": "AIS"}


def _raw_download_bytes() -> int:
    """Unique raw inputs: the selected Sentinel-1 scenes (catalogue sizes, since the SAFE archives are
    deleted after processing) plus every raw file kept on disk for the other sources."""
    from msp.pipeline.s1 import load_scene_list

    s1 = sum(int(s.get("size_bytes") or 0) for s in load_scene_list())
    other = sum(f.stat().st_size for d in paths.RAW.iterdir() if d.is_dir() and d.name != "s1"
                for f in d.rglob("*") if f.is_file())
    return s1 + other


FRESHNESS_WHY = {
    "ais": "AIS is needed at each radar pass, so the newest position must be close to the pass time.",
    "ecmwf-forecast": "A new forecast run is published twice a day and fetched in the next 6-hour slot.",
    "sentinel-1": "The selected summer passes repeat about weekly on each orbit, with gaps of several days. Winter radar is a sample chosen by sea state, so it is not monitored for freshness there; AIS is checked at every winter pass.",
    "sentinel-2": "The optical satellites pass every two to three days; cloudy days still deliver a file.",
    "sentinel-3": "Sea temperature is taken every second day.",
}


def _hours(td) -> float:
    return round(td.total_seconds() / 3600, 2)


def _delivery(rl: pd.DataFrame) -> list[dict]:
    """Time from observation to provider publication to finished product, per source (replay clock plus
    measured processing time)."""
    from msp.flows import ingest_slot, slot
    from msp.pipeline.s1 import load_scene_list
    from msp.sources.ecmwf import PUBLISH_DELAY

    proc = rl[rl.status.isin(["completed", "recovered"])].groupby(["replay_time", "flow"]).duration_s.sum()
    rows = []
    for sc in load_scene_list():
        t_obs = pd.Timestamp(sc.get("end") or sc["start"])
        t_pub = pd.Timestamp(sc.get("published") or sc["start"])
        t_slot = pd.Timestamp(ingest_slot(sc))
        secs = sum(float(proc.get((t_slot, f), 0.0)) for f in ("s1_ingest", "ship_detect", "metocean_ingest", "match"))
        rows.append({"source": "Sentinel-1 radar", "season": sc.get("season", "summer"),
                     "to_published_h": _hours(t_pub - t_obs), "to_product_h": _hours(t_slot - t_pub) + round(secs / 3600, 2)})
    ais = rl[(rl.flow == "ais_ingest") & rl.status.isin(["completed", "recovered"])]
    for sc in load_scene_list():
        later = ais[ais.replay_time >= pd.Timestamp(ingest_slot(sc))].sort_values("replay_time")
        if later.empty:
            continue
        r = later.iloc[0]
        # AIS is fetched when its radar pass reaches the pipeline: its own delay is measured from that slot,
        # so a late-published radar scene does not show up as slow AIS
        rows.append({"source": "AIS", "season": sc.get("season", "summer"), "to_published_h": None,
                     "to_product_h": _hours(r.replay_time - pd.Timestamp(ingest_slot(sc))) + round(float(r.duration_s or 0) / 3600, 2)})
    ec = rl[(rl.flow == "ecmwf_ingest") & rl.status.isin(["completed", "recovered"])]
    for r in ec.itertuples():
        m = re.search(r"run (\d{4}-\d{2}-\d{2} \d{2})z", r.message or "")
        if not m:
            continue
        run = pd.Timestamp(m.group(1) + ":00", tz="UTC")
        rows.append({"source": "ECMWF forecast", "season": "summer", "to_published_h": _hours(PUBLISH_DELAY),
                     "to_product_h": _hours(pd.Timestamp(slot(run + PUBLISH_DELAY)) - (run + PUBLISH_DELAY)) + round(float(r.duration_s or 0) / 3600, 2)})
    df = pd.DataFrame(rows)
    out = []
    for (src, season), g in df.groupby(["source", "season"]):
        tot = g.to_product_h + g.to_published_h.fillna(0)
        out.append({"source": src, "season": season, "count": int(len(g)),
                    "published_median_h": _r(g.to_published_h.median(), 1) if g.to_published_h.notna().any() else None,
                    "published_max_h": _r(g.to_published_h.max(), 1) if g.to_published_h.notna().any() else None,
                    "product_median_h": _r(g.to_product_h.median(), 1), "product_max_h": _r(g.to_product_h.max(), 1),
                    "total_median_h": _r(tot.median(), 1), "total_max_h": _r(tot.max(), 1)})
    return out


def _late_published(hours: float = 24.0) -> list[dict]:
    """Radar scenes the provider published more than `hours` after acquisition (catalogue metadata)."""
    from msp.pipeline.s1 import load_scene_list

    out = []
    for sc in load_scene_list():
        t_obs = pd.Timestamp(sc.get("end") or sc["start"])
        t_pub = pd.Timestamp(sc.get("published") or sc["start"])
        if (t_pub - t_obs) > pd.Timedelta(hours=hours):
            out.append({"time": t_obs.strftime("%Y-%m-%dT%H:%M:%SZ"), "platform": sc["platform"],
                        "season": sc.get("season", "summer"), "published_h": _hours(t_pub - t_obs)})
    return out


def _alert_episodes(fr: pd.DataFrame) -> list[dict]:
    """Runs of non-fresh checks per source: when the alert was raised and when it cleared."""
    eps = []
    for src, g in fr.sort_values("replay_time").groupby("source"):
        cur = None
        for r in g.itertuples():
            if r.status != "fresh" and cur is None:
                cur = {"source": src, "label": SOURCE_LABELS.get(src, src), "status": r.status, "raised": r.replay_time, "cleared": None}
            elif r.status == "fresh" and cur is not None:
                cur["cleared"] = r.replay_time
                eps.append(cur)
                cur = None
        if cur is not None:
            eps.append(cur)
    return sorted(eps, key=lambda e: e["raised"], reverse=True)


def _incidents(rl: pd.DataFrame) -> list[dict]:
    """The three injected incidents as the run log recorded them (config switches ops.incidents)."""
    msg = rl.message.fillna("")
    out = []
    for kind, title, mask in (
        ("timeout", "Download time-out, retried", msg.str.contains("time-out") | ((rl.flow == "ecmwf_ingest") & (rl.status == "recovered"))),
        ("corrupt", "Damaged file caught, quarantined, fetched again", msg.str.contains("damaged file")),
        ("outage", "AIS outage: alert, then gap filled", msg.str.contains("outage") | msg.str.contains("gap filled")),
    ):
        g = rl[mask].sort_values(["replay_time", "attempt"])
        if g.empty:
            continue
        out.append({"kind": kind, "title": title, "rows": [
            {"replay_time": r.replay_time, "flow": FLOW_LABELS.get(r.flow, r.flow), "task": r.task, "status": r.status,
             "attempt": int(r.attempt), "message": r.message} for r in g.itertuples()]})
    return out


def export_ops() -> dict:
    cfg = load_config()
    winter_end = pd.Timestamp(cfg.winter.end, tz="UTC") + pd.Timedelta(days=2)
    rl = db.query_df("SELECT flow, task, replay_time, status, attempt, duration_s, records, bytes, message FROM run_log")
    runs = rl[rl.status != "alert"]
    flows = []
    for f, g in runs.groupby("flow"):
        flows.append({"flow": f, "label": FLOW_LABELS.get(f, f), "runs": int(len(g)),
                      "completed": int((g.status == "completed").sum()), "retried": int((g.status == "retried").sum()),
                      "failed": int((g.status == "failed").sum()), "recovered": int((g.status == "recovered").sum()),
                      "seconds": float(g.duration_s.sum()), "records": int(g.records.fillna(0).sum()),
                      "gigabytes": round(float(g.bytes.fillna(0).sum()) / 1e9, 3)})
    order = list(FLOW_LABELS)
    flows.sort(key=lambda x: order.index(x["flow"]) if x["flow"] in order else 99)
    fr = db.query_df("SELECT source, replay_time, status, limit_hours FROM freshness ORDER BY replay_time")
    fr["window"] = np.where(fr.replay_time <= winter_end, "winter", "summer")
    fresh = []
    for (s, w), g in fr.groupby(["source", "window"]):
        fresh.append({"source": s, "window": w, "label": SOURCE_LABELS.get(s, s), "limit_hours": float(g.limit_hours.iloc[0]),
                      "why": FRESHNESS_WHY.get(s, ""),
                      "checks": int(len(g)), "fresh": int((g.status == "fresh").sum()),
                      "stale": int((g.status == "stale").sum()), "missing": int((g.status == "missing").sum()),
                      "timeline": [[t.strftime("%Y-%m-%dT%H:%M:%SZ"), st] for t, st in zip(g.replay_time, g.status, strict=True)]})
    alerts = rl[rl.status == "alert"].sort_values("replay_time")
    qc = db.query_df("SELECT product_type, product_id, check_name, passed FROM quality_checks")
    qcs = []
    for pt, g in qc.groupby("product_type"):
        fails = g[~g.passed]
        qcs.append({"product_type": pt, "products": int(g.product_id.nunique()), "checks": int(len(g)),
                    "failed_checks": int(len(fails)),
                    "failures": sorted({f"{r.product_id}: {r.check_name}" for r in fails.itertuples()})[:8]})
    cat = json.loads((paths.RESULTS / "catalogue_summary.json").read_text()) if (paths.RESULTS / "catalogue_summary.json").exists() else {}
    raw_gb = round(_raw_download_bytes() / 1e9, 1)
    facts = {"passes": int(db.query_df("SELECT count(*) n FROM passes").n.iloc[0]), "task_runs": int(len(runs)),
             "retries": int((runs.status == "retried").sum()), "recovered": int((runs.status == "recovered").sum()),
             "failed": int((runs.status == "failed").sum()),
             "raw_gigabytes": raw_gb, "stac_items": int(cat.get("items", 0)), "qc_checks": int(len(qc))}
    episodes = _alert_episodes(fr)
    obj = {"flows": flows, "freshness": fresh, "alert_count": int(len(alerts)),
           "alerts": [{"replay_time": r.replay_time, "source": r.task.split(":")[-1], "message": r.message}
                      for r in alerts.tail(12).itertuples()][::-1],
           "episodes": episodes, "incidents": _incidents(rl), "delivery": _delivery(rl), "late": _late_published(),
           "windows": {"summer": [cfg.period.start, cfg.period.end], "winter": [cfg.winter.start, cfg.winter.end]},
           "qc": qcs, "facts": facts}
    _write_json("ops.json", obj)
    (paths.RESULTS / "pipeline_facts.json").write_text(json.dumps(facts, indent=1))
    return facts


def export_catalogue(pass_images: dict) -> int:
    import pystac

    cat = pystac.Catalog.from_file(str(paths.CATALOG / "catalog.json"))
    items, cols = [], []
    sdir = OUT / "stac"
    if sdir.exists():
        shutil.rmtree(sdir)
    sdir.mkdir(parents=True)
    for col in cat.get_children():
        its = list(col.get_items())
        cols.append({"id": col.id, "title": col.title, "description": col.description, "count": len(its)})
        for it in its:
            d = it.to_dict(include_self_link=False)
            (sdir / f"{it.id}.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
            dt = it.datetime or it.common_metadata.start_datetime
            static = col.id == "reference-layers"
            prev = None
            if col.id == "sentinel-1-sigma0":
                prev = pass_images.get(it.id.replace("_sigma0", ""))
            elif col.id == "sentinel-2-cloud":
                day = f"{it.id[3:7]}-{it.id[7:9]}-{it.id[9:11]}"
                prev = f"s2/{day}.webp" if (OUT / "s2" / f"{day}.webp").exists() else f"s2/{day}_cloud.webp"
            elif col.id == "sentinel-3-sst":
                day = f"{it.id[7:11]}-{it.id[11:13]}-{it.id[13:15]}"
                prev = f"sst/{day}.webp" if (OUT / "sst" / f"{day}.webp").exists() else None
            p = it.properties
            summary = {k: p.get(k) for k in ("platform", "sat:relative_orbit", "sat:orbit_state", "sar:polarizations",
                                             "eo:cloud_cover", "msp:valid_share", "msp:detections",
                                             "forecast:reference_time", "processing:level", "msp:attribution") if k in p}
            if "sar:polarizations" in summary:
                summary["sar:polarizations"] = ", ".join(summary["sar:polarizations"])
            if "processing:software" in p:
                summary["processing:software"] = ", ".join(f"{k} {v}" for k, v in p["processing:software"].items())
            summary["assets"] = ", ".join(f"{k} ({a.media_type})" for k, a in it.assets.items())
            items.append({"id": it.id, "collection": col.id, "datetime": None if static else dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "static": static,
                          "bbox": [round(x, 4) for x in it.bbox], "preview": prev, "stac": f"stac/{it.id}.json",
                          "summary": summary})
    items.sort(key=lambda x: x["datetime"] or "")
    _write_json("catalogue.json", {"collections": cols, "items": items})
    return len(items)


def snapshot_hash() -> str:
    h = hashlib.sha256()
    for f in sorted(paths.RESULTS.glob("*.*")):
        if f.suffix in (".csv", ".json"):
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def _by_season(name: str, col: str, size: bool = False) -> dict:
    """{season: rows} from a result file with a season column."""
    df = pd.read_csv(paths.RESULTS / f"{name}.csv")
    out = {}
    for season, g in df.groupby("season", sort=False):
        if size:
            out[season] = [{"cls": r[col], "n": int(r.n), "median_length_m": float(r.median_length_m),
                            "share_over_60m": float(r.share_over_60m)} for _, r in g.iterrows() if r.n > 0]
        else:
            out[season] = [{"cls": r[col], "rate": r.rate, "lo": r.lo, "hi": r.hi, "n": int(r.n), "events": int(r.events)}
                           for _, r in g.iterrows() if r.n > 0]
    return out


def _stories(passes: list[dict]) -> list[dict]:
    """Passes worth a look, each chosen by a rule from the results (never by hand)."""
    out = []

    def add(key, title, p):
        if p and not any(o["pass"] == p["id"] for o in out):
            out.append({"key": key, "title": title, "pass": p["id"]})

    winter = [p for p in passes if p["season"] == "winter"]
    summer = [p for p in passes if p["season"] == "summer"]
    by = lambda ps, f: max(ps, key=f) if ps else None  # noqa: E731
    add("stormiest", "Stormiest winter pass", by(winter, lambda p: p.get("sea_waves") or 0))
    add("ai_struggles", "Where the AI struggles most", by(passes, lambda p: p.get("ai_error_share") or 0))
    add("busiest", "Busiest pass", by(passes, lambda p: p["ais_visible"]))
    add("no_ais", "Most bright spots without AIS", by(passes, lambda p: p["unmatched"]))
    clear = [p for p in summer if p.get("s2_clear")]
    add("clear", "A rare clear day", by(clear, lambda p: -(p.get("s2_cloud") or 1)))
    add("calm", "Calmest sea", by(passes, lambda p: -(p.get("sea_waves") or 99)))
    return out


def export_summary(passes: list[dict]) -> dict:
    cfg = load_config()
    rs = json.loads((paths.RESULTS / "research_summary.json").read_text())
    ml = json.loads((paths.RESULTS / "ml_scores.json").read_text())
    sea = pd.read_csv(paths.RESULTS / "ml_by_sea_state.csv")
    ml["by_sea_state"] = {k: g.drop(columns="season").to_dict("records") for k, g in sea.groupby("season", sort=False)}
    le = pd.read_csv(paths.RESULTS / "ml_length_error.csv").replace({np.nan: None})
    ml["length_error"] = {k: g.drop(columns="season").to_dict("records") for k, g in le.groupby("season", sort=False)}
    lr = pd.read_csv(paths.RESULTS / "logistic_regression.csv")
    models = json.loads((paths.RESULTS / "logistic_models.json").read_text())
    curve = json.loads((paths.RESULTS / "wave_curve.json").read_text())
    stormy = max(passes, key=lambda p: (p.get("sea_waves") or 0))
    cat = json.loads((paths.RESULTS / "catalogue_summary.json").read_text())

    def period(sec):
        p0, p1 = datetime.fromisoformat(sec.start), datetime.fromisoformat(sec.end)
        return {"start": sec.start, "end": sec.end, "weeks": round((p1 - p0).days / 7)}

    summary = {
        "periods": {"summer": period(cfg.period), "winter": period(cfg.winter)},
        "period": period(cfg.period),
        "area": {"name": cfg.area.name, "bbox": list(cfg.area.bbox)},
        "counts": {"passes": len(passes), "passes_summer": sum(p["season"] == "summer" for p in passes),
                   "passes_winter": sum(p["season"] == "winter" for p in passes),
                   "detections": int(sum(p["detections"] for p in passes)),
                   "ais_ships_visible": int(sum(p["ais_visible"] for p in passes)),
                   "eval_passes": rs["passes_eval"], "winter_passes": rs["passes_winter"], "dev_passes": rs["passes_dev"],
                   "stac_items": cat["items"]},
        "seasons": rs["seasons"],
        "by_length": _by_season("detection_by_length", "length_cls"), "by_wind": _by_season("detection_by_wind", "wind_cls"),
        "by_wave": _by_season("detection_by_wave", "wave_cls"), "unmatched_by_wave": _by_season("unmatched_by_wave", "wave_cls"),
        "unmatched_by_wind": _by_season("unmatched_by_wind", "wind_cls"),
        "size_by_wave": _by_season("ship_size_by_wave", "wave_cls", size=True),
        "size_by_wind": _by_season("ship_size_by_wind", "wind_cls", size=True),
        "logistic": {k: g.drop(columns="model").to_dict("records") for k, g in lr.groupby("model", sort=False)},
        "models": models, "tool_range": rs["tool_range"], "cloud": rs["cloud_blindness"], "ml": ml,
        "interesting_pass": stormy["id"], "stories": _stories(passes), "wave_curve": curve,
        "typical_ship": rs.get("typical_ship"), "snapshot": snapshot_hash(),
    }
    _write_json("summary.json", summary)
    return summary


# ------------------------------------------------------------------ main

def export_hero(p: dict, view: tuple[float, float, float, float] = (4.0, 60.4, 5.6, 60.95)) -> str:
    """Start-page picture: one radar pass with the ships found and missed (Web Mercator, WebP, no metadata)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import shapely

    def merc(lat):
        return np.degrees(np.log(np.tan(np.pi / 4 + np.radians(np.asarray(lat, dtype=float)) / 2)))

    b = np.array(p["bounds"])
    img = np.asarray(Image.open(OUT / p["image"]).convert("RGBA"))
    land = gpd.read_file(OUT / "land.geojson")
    det = gpd.read_file(OUT / f"detections/{p['id']}.geojson")
    ais = gpd.read_file(OUT / f"ais/{p['id']}.geojson")
    fig = plt.figure(figsize=(9, 6.3), dpi=150)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_facecolor("#0e2a3d")
    ax.imshow(img, extent=(b[:, 0].min(), b[:, 0].max(), merc(b[:, 1].min()), merc(b[:, 1].max())), zorder=1, aspect="auto")
    geoms = shapely.transform(land.geometry.values, lambda c: np.column_stack([c[:, 0], merc(c[:, 1])]))
    gpd.GeoSeries(geoms).plot(ax=ax, color="#e7e2d6", edgecolor="#b9b09c", linewidth=0.3, zorder=2)
    ay, dy = merc(ais.geometry.y.values), merc(det.geometry.y.values)
    ax.scatter(ais.geometry.x, ay, s=260, facecolors="none", edgecolors="white", linewidths=6, zorder=3)
    ax.scatter(ais.geometry.x, ay, s=260, facecolors="none", linewidths=3.2, zorder=4,
               edgecolors=["#4c6ef5" if d else "#e64980" for d in ais.detected])
    ax.scatter(det.geometry.x, dy, s=60, c=["#10a37f" if m else "#f0803c" for m in det.matched],
               edgecolors="white", linewidths=1.2, zorder=5)
    ax.set_xlim(view[0], view[2])
    ax.set_ylim(merc(view[1]), merc(view[3]))
    ax.set_aspect("equal")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="#0e2a3d", metadata={"Software": None})
    plt.close(fig)
    buf.seek(0)
    Image.open(buf).convert("RGB").save(OUT / "hero.webp", "WEBP", quality=82, method=6)
    return "hero.webp"


def run(images: bool = True) -> dict:
    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    bbox = list(cfg.area.bbox)
    export_land()
    s2 = export_s2() if images else json.loads((OUT / "s2.json").read_text())
    sst = export_sst() if images else json.loads((OUT / "sst.json").read_text())
    good_sst = [x for x in sst if x["image"]]
    ps = db.query_df("SELECT pass_id, start_time, platform, relative_orbit, orbit_direction, engine FROM passes ORDER BY start_time")
    sp = ps.merge(pass_splits(), on="pass_id").set_index("pass_id")
    from msp.pipeline.s1 import load_scene_list

    meta = {s["pass_id"]: s for s in load_scene_list()}
    # sea state over open sea at each pass (wave-model cells at least 3 km from land), as in the winter selection
    from msp.pipeline.winter import sea_state_at_passes

    sea = sea_state_at_passes([m for m in meta.values() if m["pass_id"] in set(ps.pass_id)],
                              paths.PRODUCTS / "metocean" / "metno_waves_wind.nc",
                              paths.PRODUCTS / "landmask" / "land.shp").set_index("pass_id")
    pred = pd.read_parquet(paths.WORK / "ml_predictions.parquet") if (paths.WORK / "ml_predictions.parquet").exists() else None
    ai_err = {}
    if pred is not None:
        lab = pred[pred.label != "ambiguous"]
        wrong = ((lab.p_cnn >= 0.5) != (lab.label == "ship"))
        ai_err = lab.assign(w=wrong).groupby("pass_id").agg(n=("w", "size"), e=("w", "sum")).to_dict("index")
    passes, pass_images = [], {}
    for r in ps.itertuples():
        t = r.start_time.to_pydatetime().astimezone(UTC)
        corners = export_radar(r.pass_id) if images else None
        pass_images[r.pass_id] = f"radar/{r.pass_id}.webp"
        wave_corners = export_waves(r.pass_id, t, bbox) if images else None
        wind = export_wind(r.pass_id, t, bbox)
        st = export_points(r.pass_id)
        day = t.strftime("%Y-%m-%d")
        s2day = next((x["day"] for x in s2 if x["day"] == day), None)
        near = sorted(good_sst, key=lambda x: abs((datetime.fromisoformat(x["day"]) - datetime.fromisoformat(day)).days))
        sstday = near[0]["day"] if near and abs((datetime.fromisoformat(near[0]["day"]) - datetime.fromisoformat(day)).days) <= 4 else None
        ws = sample([5.0], [60.6], [t])
        passes.append({"id": r.pass_id, "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "label": t.strftime("%d %b %H:%M UTC"), "platform": r.platform,
                       "orbit": int(r.relative_orbit), "direction": r.orbit_direction.lower(), "engine": r.engine,
                       "bounds": corners, "image": f"radar/{r.pass_id}.webp", "image_lite": f"radar/{r.pass_id}_lite.webp",
                       "waves_image": f"waves/{r.pass_id}.webp", "wind": wind,
                       "wave_bounds": json.loads(wave_corners) if wave_corners else None,
                       **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in st.items()},
                       "wave_max": _r(float(np.nanmax([f["properties"]["waves"] or 0 for f in json.loads((OUT / f"ais/{r.pass_id}.geojson").read_text())["features"]] or [0])), 2),
                       "unmatched": st["detections"] - st["matched"], "s2_day": s2day, "sst_day": sstday,
                       "split": sp.loc[r.pass_id, "ml_split"], "season": sp.loc[r.pass_id, "season"],
                       "published": (meta.get(r.pass_id) or {}).get("published", "")[:19] + "Z" if (meta.get(r.pass_id) or {}).get("published") else None,
                       "s2_cloud": next((x["cloud_share"] for x in s2 if x["day"] == day), None),
                       "s2_clear": bool(next((x["truecolor"] for x in s2 if x["day"] == day), None)),
                       "ai_errors": int(ai_err.get(r.pass_id, {}).get("e", 0)),
                       "ai_error_share": _r(ai_err[r.pass_id]["e"] / ai_err[r.pass_id]["n"], 3) if ai_err.get(r.pass_id, {}).get("n", 0) >= 10 else None,
                       "wind_at_fedje": _r(ws.wind_speed.iloc[0], 1),
                       "sea_waves": _r(sea.hs_median.get(r.pass_id), 2), "sea_waves_max": _r(sea.hs_max.get(r.pass_id), 2),
                       "sea_wind": _r(sea.wind_median.get(r.pass_id), 1), "sea_class": sea.stratum.get(r.pass_id)})
    _write_json("passes.json", passes)
    export_grid_series(bbox)
    export_clouds([p for p in passes if p["season"] == "summer"], s2, sst, cfg.period.start, cfg.period.end)
    n_game = export_game()
    ops = export_ops()
    n_items = export_catalogue(pass_images)
    summary = export_summary(passes)
    if images:
        export_hero(next(p for p in passes if p["id"] == summary["interesting_pass"]))
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    return {"passes": len(passes), "game_rounds": n_game, "catalogue_items": n_items, "ops": ops,
            "snapshot": summary["snapshot"], "megabytes": round(size / 1e6, 1)}


__all__ = ["run", "transform_bounds"]
