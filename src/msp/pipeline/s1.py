"""Sentinel-1 chain for one pass: SAFE -> calibrated sigma0 (SNAP or Python) -> COG product -> detections.

Files:
- data/raw/s1/<pass>.zip                  SAFE download (deleted after processing when keep_raw is False)
- data/work/s1/<pass>_<engine>.tif        10 m float32 sigma0 (linear), VV and VH, working file
- data/products/s1/<pass>_sigma0_db.tif   20 m COG, sigma0 in dB x 100 as int16 (the catalogued product)
- data/products/detections/<pass>.parquet GeoParquet of detections
- data/work/chips/<pass>.npz              64 x 64 VV/VH cut-outs around every detection (for the classifier)
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
from rasterio.enums import Resampling
from shapely import wkt
from shapely.geometry import MultiPolygon, shape
from shapely.ops import unary_union

from msp import paths
from msp.config import load_config
from msp.detect.cfar import DetectorParams, detect
from msp.sources.landmask import sea_mask_for_grid
from msp.sources.sentinel2 import write_cog

DB_SCALE = 100.0
DB_NODATA = -32768


def manifest_times(safe_zip: Path) -> tuple[datetime, datetime]:
    with zipfile.ZipFile(safe_zip) as zf:
        name = next(n for n in zf.namelist() if n.endswith("manifest.safe"))
        text = zf.read(name).decode("utf-8", "ignore")
    start = re.search(r"<safe:startTime>([^<]+)</safe:startTime>", text).group(1)
    stop = re.search(r"<safe:stopTime>([^<]+)</safe:stopTime>", text).group(1)

    def parse(s: str) -> datetime:
        return datetime.fromisoformat(s.rstrip("Z")[:26]).replace(tzinfo=UTC)

    return parse(start), parse(stop)


def work_raster(pass_id: str, engine: str) -> Path:
    return paths.WORK / "s1" / f"{pass_id}_{engine}.tif"


def product_cog(pass_id: str) -> Path:
    return paths.PRODUCTS / "s1" / f"{pass_id}_sigma0_db.tif"


def write_db_cog(src_tif: Path, out: Path, pixel_m: float = 20.0, tags: dict | None = None) -> dict:
    """Average sigma0 (linear) to pixel_m, convert to dB, store as scaled int16 COG."""
    with rasterio.open(src_tif) as src:
        f = pixel_m / abs(src.transform.a)
        h, w = int(round(src.height / f)), int(round(src.width / f))
        lin = src.read(out_shape=(src.count, h, w), resampling=Resampling.average, masked=False)
        transform = src.transform * src.transform.scale(src.width / w, src.height / h)
        crs = src.crs.to_string()
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(lin)
    q = np.where(np.isfinite(db), np.clip(np.round(db * DB_SCALE), -32767, 32767), DB_NODATA).astype(np.int16)
    write_cog(out, q, transform, crs, DB_NODATA, tags=tags, resampling="average")
    valid = np.isfinite(db)
    return {"width": w, "height": h, "nodata_share": round(1 - float(valid[0].mean()), 4),
            "vv_db_median": round(float(np.nanmedian(db[0][valid[0]])), 2) if valid[0].any() else None}


def valid_sea_area(cog: Path, land_shp: Path, structures: Path | None = None, step: int = 5) -> MultiPolygon:
    """Imaged sea area of a pass (coarse polygon, 100 m cells from the 20 m product), with the same
    buffered land mask and structure mask as the detector. AIS ships inside it count as visible."""
    with rasterio.open(cog) as src:
        db = src.read(1, out_shape=(src.height // step, src.width // step), resampling=Resampling.nearest)
        transform = src.transform * src.transform.scale(src.width / db.shape[1], src.height / db.shape[0])
        nodata = src.nodata
    sea = sea_mask_for_grid(land_shp, structures, transform, db.shape[1], db.shape[0])
    ok = ((db != nodata) & sea).astype(np.uint8)
    polys = [shape(g) for g, v in rasterio.features.shapes(ok, mask=ok == 1, transform=transform) if v == 1]
    u = unary_union(polys).buffer(0)
    return u if isinstance(u, MultiPolygon) else MultiPolygon([u])


def candidates_path(pass_id: str) -> Path:
    return paths.WORK / "candidates" / f"{pass_id}.parquet"


def finalize(pass_id: str, params: DetectorParams) -> gpd.GeoDataFrame:
    """Second detector stage: keep candidates above the contrast and brightness thresholds; write the
    detection GeoParquet product. det_idx stays the candidate index (links to the cut-outs)."""
    cand = gpd.read_parquet(candidates_path(pass_id))
    keep = (cand["scr_db"] >= params.min_scr_db) & (cand["peak_vv_db"] >= params.min_peak_db)
    det = cand[keep].copy()
    out = paths.PRODUCTS / "detections" / f"{pass_id}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    det.to_parquet(out)
    return det


def detect_pass(pass_id: str, engine: str, sea_land_shp: Path, structures: Path, params: DetectorParams,
                chip: int = 64) -> gpd.GeoDataFrame:
    """First detector stage: CFAR on the 10 m working raster; stores all candidates and their cut-outs."""
    cfg = load_config()
    src_path = work_raster(pass_id, engine)
    with rasterio.open(src_path) as src:
        vv = src.read(1)
        vh = src.read(2)
        transform, crs = src.transform, src.crs.to_string()
    vv[~(vv > 0)] = np.nan  # SNAP writes 0 outside the imaged swath
    vh[~(vh > 0)] = np.nan
    sea = sea_mask_for_grid(sea_land_shp, structures, transform, vv.shape[1], vv.shape[0])
    det = detect(vv, vh, sea, transform, params, pixel_m=float(cfg.area.pixel_size_m))
    gdf = gpd.GeoDataFrame(det, geometry=gpd.points_from_xy(det["x"], det["y"]), crs=crs)
    ll = gdf.to_crs("EPSG:4326")
    gdf["lon"] = ll.geometry.x.values
    gdf["lat"] = ll.geometry.y.values
    gdf.insert(0, "pass_id", pass_id)
    gdf.insert(1, "det_idx", np.arange(len(gdf), dtype=np.int32))
    out = candidates_path(pass_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out)
    # Cut-outs (VV, VH) centered on each detection, sigma0 in dB, NaN outside the image
    half = chip // 2
    chips = np.full((len(gdf), 2, chip, chip), np.nan, dtype=np.float16)
    for i, (r, c) in enumerate(zip(det["row"].round().astype(int), det["col"].round().astype(int), strict=True)):
        r0, c0 = r - half, c - half
        rs0, cs0 = max(r0, 0), max(c0, 0)
        rs1, cs1 = min(r0 + chip, vv.shape[0]), min(c0 + chip, vv.shape[1])
        for b, arr in enumerate((vv, vh)):
            win = arr[rs0:rs1, cs0:cs1].astype(np.float32)
            with np.errstate(divide="ignore", invalid="ignore"):
                chips[i, b, rs0 - r0:rs1 - r0, cs0 - c0:cs1 - c0] = 10 * np.log10(np.clip(win, 1e-6, None))
    cdir = paths.WORK / "chips"
    cdir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cdir / f"{pass_id}.npz", chips=chips, det_idx=gdf["det_idx"].values)
    return gdf


def pass_record(scene: dict, safe_zip: Path) -> dict:
    start, stop = manifest_times(safe_zip)
    fp = wkt.loads(scene["footprint_wkt"])
    return {"pass_id": scene["pass_id"], "platform": scene["platform"], "start_time": start, "stop_time": stop,
            "relative_orbit": scene["relative_orbit"], "orbit_direction": scene["orbit_direction"],
            "footprint": fp if isinstance(fp, MultiPolygon) else MultiPolygon([fp])}


SEASONS = {  # season: (catalogue search results, frozen pass list)
    "summer": ("s1_candidates.json", "s1_passes.txt"),
    "winter": ("s1_candidates_winter.json", "s1_passes_winter.txt"),
}


def load_scene_list(season: str | None = None) -> list[dict]:
    """Selected passes with catalogue metadata, pass_id and season (both seasons unless one is named).

    A season whose pass list does not exist yet is simply absent.
    """
    from msp.config import ROOT
    from msp.sources.cdse import S1Scene

    out = []
    for name, (cand_file, list_file) in SEASONS.items():
        lf = ROOT / "config" / list_file
        if (season and name != season) or not lf.exists():
            continue
        cands = {S1Scene(**d).pass_id: d | {"pass_id": S1Scene(**d).pass_id, "season": name}
                 for d in json.loads((paths.WORK / cand_file).read_text())}
        # one pass id per line; anything after "#" is a comment
        selected = [ln.split("#")[0].strip() for ln in lf.read_text().splitlines()]
        selected = [x for x in selected if x]
        out += [cands[p] for p in selected]
    return sorted(out, key=lambda d: d["start"])


def pass_splits() -> pd.DataFrame:
    """Research and ML splits over the frozen pass lists, by acquisition time (summer) or season (winter).

    Splits never depend on which passes happen to be processed already, so tuning can start as soon as
    the development passes exist and every later step sees the same assignment.
    """
    from msp.config import load_config
    from msp.match.rules import assign_splits

    cfg = load_config()
    scenes = pd.DataFrame([{"pass_id": s["pass_id"], "start_time": pd.Timestamp(s["start"])}
                           for s in load_scene_list("summer")])
    sp = assign_splits(scenes, float(cfg.evaluation.dev_fraction), 0.5, float(cfg.ml.test_fraction))
    sp = sp[["pass_id", "research_split", "ml_split"]].assign(season="summer")
    # winter passes are a second held-out test set: never development, training or calibration
    win = pd.DataFrame({"pass_id": [s["pass_id"] for s in load_scene_list("winter")]})
    if len(win):
        sp = pd.concat([sp, win.assign(research_split="winter", ml_split="winter", season="winter")], ignore_index=True)
    return sp
