"""Prefect flows: one per source, plus detection, matching, catalogue, operations and the backfill.

Every task retries on failure, caches its result by input (re-running a finished step is free),
writes a run_log row per attempt, and respects concurrency limits by tag:
- "snap": at most one SNAP run at a time (memory)
- "cdse": at most two downloads from Copernicus Data Space at a time

The backfill replays the study period day by day on a replay clock, as if the pipeline were running
live round the clock, and checks freshness against that clock four times a day.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from prefect import flow, task
from prefect.cache_policies import INPUTS, TASK_SOURCE

from msp import db, paths
from msp.config import load_config
from msp.ops import runlog
from msp.ops.runlog import logged

UTC = UTC
CACHE = INPUTS + TASK_SOURCE
LAND = paths.PRODUCTS / "landmask" / "land.shp"
LAND_BUFFERED = paths.PRODUCTS / "landmask" / "land_buffered.shp"
STRUCTURES = paths.PRODUCTS / "structures" / "structures.parquet"
ITEMS = paths.WORK / "stac_items"


def _period() -> tuple[datetime, datetime]:
    cfg = load_config()
    s = datetime.fromisoformat(cfg.period.start).replace(tzinfo=UTC)
    e = datetime.fromisoformat(cfg.period.end).replace(tzinfo=UTC) + timedelta(hours=23, minutes=59)
    return s, e


def ingest_slot(scene: dict) -> datetime:
    """Replay slot in which a radar scene is fetched: the first 6-hour slot after the provider has
    published it (as in live operation), never before the acquisition itself."""
    t = max(scene["start"], scene.get("published") or scene["start"])
    return slot(t)


def slot(t: datetime | str) -> datetime:
    """Replay-clock slot in which data acquired at t is processed: the next 6-hour boundary after t."""
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    t = t.astimezone(UTC)
    base = t.replace(minute=0, second=0, microsecond=0, hour=(t.hour // 6) * 6)
    return base + timedelta(hours=6)


def _save_item(item) -> None:
    ITEMS.mkdir(parents=True, exist_ok=True)
    (ITEMS / f"{item.id}.json").write_text(json.dumps(item.to_dict(include_self_link=False), default=str),
                                           encoding="utf-8")


def _incident(name: str):
    """An incident switch from config/pipeline.yaml (ops.incidents), or None when it is off."""
    return ((load_config().get("ops") or {}).get("incidents") or {}).get(name)


def _first_time(name: str) -> bool:
    """True only the first time an incident fires, so its retry or re-fetch succeeds."""
    flag = paths.WORK / "ops" / f"incident_{name}.done"
    if flag.exists():
        return False
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("fired", encoding="utf-8")
    return True


def _simulated_failure(source: str, key: str) -> None:
    """Incident 1: a download that times out on its first attempt; the task's retry succeeds.

    Switch: ops.incidents.download_timeout = "<source>:<key>" (environment MSP_SIMULATE_FAILURE overrides).
    """
    want = os.environ.get("MSP_SIMULATE_FAILURE") or _incident("download_timeout") or ""
    if want == f"{source}:{key}" and _first_time(f"timeout_{source}_{key.replace(':', '')}"):
        raise TimeoutError(f"simulated download time-out for {source} {key}")


def _already_processed(pass_id: str) -> bool:
    """A pass whose product, pass record and first-stage candidates exist (with the current mask) is
    never downloaded or processed again, even when the task code has changed since."""
    from msp.pipeline.s1 import candidates_path, product_cog

    marker = candidates_path(pass_id).with_suffix(".mask")
    return (product_cog(pass_id).exists() and candidates_path(pass_id).exists() and marker.exists()
            and marker.read_text() == _mask_fingerprint()
            and not db.query_df("SELECT 1 FROM passes WHERE pass_id = %s", (pass_id,)).empty)


def _ais_outage(t: datetime | None) -> bool:
    """Incident 3: the AIS service is unavailable for a while on the replay clock."""
    o = _incident("ais_outage")
    if not o or t is None:
        return False
    s = datetime.fromisoformat(o["start"]).replace(tzinfo=UTC)
    return s <= t < s + timedelta(hours=float(o["hours"]))


# ------------------------------------------------------------------ Sentinel-1

@task(retries=3, retry_delay_seconds=[10, 30, 90], tags=["cdse"], cache_policy=CACHE)
@logged("s1_ingest", max_attempts=4)
def s1_download(scene: dict, replay_time: datetime | None = None) -> dict:
    import dataclasses

    from msp.sources.cdse import CdseSession, S1Scene

    fields = {f.name for f in dataclasses.fields(S1Scene)}
    s = S1Scene(**{k: v for k, v in scene.items() if k in fields})
    out = paths.RAW / "s1" / f"{s.pass_id}.zip"
    r = CdseSession().download(s, out)
    return {"records": 1, "bytes": r["bytes"], "message": "cached" if r["cached"] else f"{r['seconds']} s"}


@task(retries=1, retry_delay_seconds=30, tags=["snap"], cache_policy=CACHE)
@logged("s1_ingest", max_attempts=2)
def s1_process(pass_id: str, engine: str = "snap", replay_time: datetime | None = None) -> dict:
    """SNAP graph; on failure the pure-Python chain takes over for this pass."""
    from msp.pipeline.s1 import work_raster
    from msp.sar import fallback, snap

    safe = paths.RAW / "s1" / f"{pass_id}.zip"
    for eng in ([engine, "python"] if engine == "snap" else [engine]):
        out = work_raster(pass_id, eng)
        if out.exists():
            return {"engine": eng, "records": 1, "bytes": out.stat().st_size, "message": f"{eng} cached"}
        try:
            r = snap.run_snap(safe, out, log_file=paths.WORK / "logs" / f"snap_{pass_id}.log") if eng == "snap" \
                else fallback.process_scene(safe, out)
            return {"engine": eng, "records": 1, "bytes": r["bytes"], "message": f"{eng} {r['seconds']} s"}
        except Exception as exc:
            runlog.alert("sentinel-1", f"{eng} processing failed for {pass_id}: {str(exc)[:200]}", replay_time)
            if eng == "python":
                raise
    raise RuntimeError("no engine succeeded")


@task(cache_policy=CACHE)
@logged("s1_ingest")
def s1_product(scene: dict, engine: str, replay_time: datetime | None = None) -> dict:
    """COG product, pass record with the imaged sea area, STAC item, quality checks."""
    from msp.catalog.stac import s1_item
    from msp.pipeline.s1 import pass_record, product_cog, valid_sea_area, work_raster, write_db_cog
    from msp.quality import checks

    pid = scene["pass_id"]
    safe = paths.RAW / "s1" / f"{pid}.zip"
    rec = pass_record(scene, safe)
    cog = product_cog(pid)
    stats = write_db_cog(work_raster(pid, engine), cog,
                         tags={"ACQUISITION_TIME": rec["start_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "PROCESSING_ENGINE": engine, "BANDS": "VV,VH", "UNITS": "dB x 100"})
    area = valid_sea_area(cog, LAND_BUFFERED, STRUCTURES)
    db.execute("DELETE FROM passes WHERE pass_id = %s", (pid,))
    db.execute("""INSERT INTO passes (pass_id, platform, start_time, stop_time, relative_orbit, orbit_direction,
                  engine, footprint, valid_area) VALUES (%s,%s,%s,%s,%s,%s,%s,
                  ST_Multi(ST_GeomFromText(%s, 4326)), ST_Multi(ST_GeomFromText(%s, 32632)))""",
               (pid, rec["platform"], rec["start_time"], rec["stop_time"], rec["relative_orbit"],
                rec["orbit_direction"], engine, rec["footprint"].wkt, area.wkt))
    _save_item(s1_item(rec, cog, engine, ITEMS))
    cfg = load_config()
    res = checks.store(pid, "sentinel-1-sigma0",
                       checks.check_cog(cog, cfg.area.crs, cfg.area.bbox, (-45.0, 25.0), max_nodata=0.6, scale=0.01,
                                        min_overlap=0.6))
    return {"records": 1, "bytes": cog.stat().st_size, "message": f"QC failed: {res['failed']}" if res["failed"]
            else "QC ok", **stats}


# ------------------------------------------------------------------ detection, AIS, matching

def detector_settings() -> dict:
    return dict(load_config().detector)


@task(cache_policy=CACHE)
@logged("ship_detect")
def detect_task(pass_id: str, engine: str, settings: dict, replay_time: datetime | None = None) -> dict:
    """CFAR candidates (once per pass) and the final detection list for the given detector settings."""
    from msp.detect.cfar import DetectorParams
    from msp.pipeline.s1 import candidates_path, detect_pass, finalize

    params = DetectorParams.from_config(settings)
    if not candidates_path(pass_id).exists():
        detect_pass(pass_id, engine, LAND_BUFFERED, STRUCTURES, params)
        candidates_path(pass_id).with_suffix(".mask").write_text(_mask_fingerprint())
    gdf = finalize(pass_id, params)
    db.execute("DELETE FROM detections WHERE pass_id = %s", (pass_id,))
    cols = ["pass_id", "det_idx", "geom", "lon", "lat", "n_pixels", "fill", "length_m", "width_m", "heading_deg",
            "peak_vv_db", "mean_vv_db", "peak_vh_db", "bg_vv_db", "scr_db"]
    db.copy_rows("detections", cols,
                 ((r.pass_id, r.det_idx, f"POINT({r.x} {r.y})", r.lon, r.lat, r.n_pixels, r.fill, r.length_m,
                   r.width_m, r.heading_deg, r.peak_vv_db, r.mean_vv_db, r.peak_vh_db, r.bg_vv_db, r.scr_db)
                  for r in gdf.itertuples()), geom_cols={"geom": 32632})
    return {"records": int(len(gdf))}


@task
def cleanup_task(pass_id: str, engine: str, keep_raw: bool = False) -> dict:
    """Remove the 10 m working raster (and the raw scene) once the product, candidates and cut-outs exist."""
    from msp.pipeline.s1 import candidates_path, product_cog, work_raster

    freed = 0
    if candidates_path(pass_id).exists() and product_cog(pass_id).exists():
        for f in [work_raster(pass_id, engine)] + ([] if keep_raw else [paths.RAW / "s1" / f"{pass_id}.zip"]):
            if f.exists():
                freed += f.stat().st_size
                f.unlink()
    return {"bytes": freed}


@task(retries=3, retry_delay_seconds=[10, 30, 90], cache_policy=CACHE)
@logged("ais_ingest", max_attempts=4)
def ais_task(pass_id: str, replay_time: datetime | None = None) -> dict:
    from msp.pipeline.ais_match import ingest_ais_for_pass, store_vessels
    from msp.sources import ais as ais_src

    p = db.query_df("""SELECT pass_id, start_time, stop_time, orbit_direction, ST_AsText(footprint) AS fp
                       FROM passes WHERE pass_id = %s""", (pass_id,)).iloc[0].to_dict()
    from shapely import wkt

    p["footprint"] = wkt.loads(p.pop("fp"))
    salt = ais_src.load_salt(paths.WORK / "secrets" / "vessel_salt.txt")
    r = ingest_ais_for_pass(p, salt)
    if r.get("mmsi"):
        month0 = p["start_time"].replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month1 = (month0 + timedelta(days=32)).replace(day=1)
        store_vessels(ais_src.fetch_static(r["mmsi"], month0, month1), salt)
    return {"records": r.get("interpolated", 0), "message": f"{r.get('positions', 0)} positions, {r.get('ships', 0)} ships"}


def _num(x) -> float | None:
    """Missing sea state is stored as NULL, never as 0 or NaN."""
    return float(x) if x is not None and np.isfinite(x) else None


@task(cache_policy=CACHE)
@logged("metocean_ingest")
def sea_state_task(pass_id: str, settings: dict | None = None, replay_time: datetime | None = None) -> dict:
    """Wind and waves at every detection and every AIS ship of the pass."""
    from msp.metocean.sample import sample

    t = db.query_df("SELECT start_time FROM passes WHERE pass_id = %s", (pass_id,)).start_time.iloc[0]
    n = 0
    for table, key in (("detections", "det_id"), ("ais_at_pass", "vessel_hash")):
        df = db.query_df(f"SELECT {key} AS k, lon, lat FROM {table} WHERE pass_id = %s", (pass_id,))
        if df.empty:
            continue
        s = sample(df.lon, df.lat, [t] * len(df))
        with db.connect() as conn, conn.cursor() as cur:
            cur.executemany(f"UPDATE {table} SET wind_speed = %s, wave_hs = %s WHERE pass_id = %s AND {key} = %s",
                            [(_num(a), _num(b), pass_id, k) for a, b, k in zip(s.wind_speed, s.wave_hs, df.k,
                                                                               strict=True)])
        n += len(df)
    return {"records": n}


@task(cache_policy=CACHE)
@logged("match")
def match_task(pass_id: str, settings: dict | None = None, replay_time: datetime | None = None) -> dict:
    from msp.pipeline.ais_match import match_pass

    r = match_pass(pass_id)
    return {"records": r["matched"], "message": f"{r['candidates']} candidate pairs"}


# ------------------------------------------------------------------ other sources

@task(retries=2, retry_delay_seconds=[15, 60], cache_policy=CACHE)
@logged("s2_ingest", max_attempts=3)
def s2_day_task(day: str, items_json: str, replay_time: datetime | None = None) -> dict:
    import pystac

    from msp.catalog.stac import raster_item
    from msp.quality import checks
    from msp.sources import sentinel2 as s2
    from msp.sources.landmask import sea_mask_for_grid

    cfg = load_config()
    items = [pystac.Item.from_dict(d) for d in json.loads(items_json)]
    import planetary_computer

    # signed URLs expire after about an hour: drop any token from the search and sign afresh here
    for it in items:
        for a in it.assets.values():
            a.href = a.href.split("?")[0]
    items = [planetary_computer.sign(i) for i in items]
    pix = float(cfg.sentinel2.preview_resolution_m)
    scl, tr = s2.scl_mosaic(items, cfg.area.bbox, cfg.area.crs, pix)
    sea = sea_mask_for_grid(LAND, None, tr, scl.shape[1], scl.shape[0])
    st = s2.cloud_stats(scl, sea)
    acq = s2.acquisition_time(items)
    tags = {"ACQUISITION_TIME": acq.strftime("%Y-%m-%dT%H:%M:%SZ")}
    cloud = np.where(scl == 0, 255, np.isin(scl, s2.CLOUD_CLASSES).astype(np.uint8)).astype(np.uint8)
    d = day.replace("-", "")
    cpath = paths.PRODUCTS / "s2" / f"S2_{d}_cloudmask.tif"
    s2.write_cog(cpath, cloud, tr, cfg.area.crs, 255, tags)
    nbytes = cpath.stat().st_size
    tci_path = None
    if st["cloud_share"] is not None and st["cloud_share"] < 0.6:
        tci, ttr = s2.tci_mosaic(items, cfg.area.bbox, cfg.area.crs, pix)
        tci_path = paths.PRODUCTS / "s2" / f"S2_{d}_truecolor.tif"
        s2.write_cog(tci_path, tci, ttr, cfg.area.crs, 0, tags, resampling="average")
        nbytes += tci_path.stat().st_size
    props = {"eo:cloud_cover": round(100 * st["cloud_share"], 2) if st["cloud_share"] is not None else None,
             "msp:sea_observed_share": st["sea_observed_share"], "msp:cloud_share_over_sea": st["cloud_share"],
             "constellation": "sentinel-2", "instruments": ["msi"], "license": "proprietary",
             "msp:attribution": "Contains modified Copernicus Sentinel data 2026"}
    it = raster_item(f"S2_{d}_cloud", cpath, acq, "sentinel-2-cloud", ITEMS,
                     bands=[{"name": "cloud", "description": "1 cloud, 0 clear, 255 not observed"}], extra=props)
    if "https://stac-extensions.github.io/eo/v1.1.0/schema.json" not in it.stac_extensions:
        it.stac_extensions.append("https://stac-extensions.github.io/eo/v1.1.0/schema.json")
    if tci_path:
        import pystac as ps

        from msp.catalog.stac import _rel

        it.add_asset("truecolor", ps.Asset(href=_rel(tci_path, ITEMS), media_type=ps.MediaType.COG,
                                           roles=["visual"], title="True-color preview"))
    _save_item(it)
    rows = paths.WORK / "s2_cloud.jsonl"
    with open(rows, "a", encoding="utf-8") as f:
        f.write(json.dumps({"day": day, "time": tags["ACQUISITION_TIME"], **st, "tiles": len(items)}) + "\n")
    res = checks.store(f"S2_{d}", "sentinel-2-cloud",
                       checks.check_cog(cpath, cfg.area.crs, cfg.area.bbox, (0, 255), max_nodata=1.0))
    return {"records": len(items), "bytes": nbytes, "message": f"cloud over sea {st['cloud_share']}, QC failed: {res['failed']}"}


@task(retries=3, retry_delay_seconds=[10, 30, 90], tags=["cdse"], cache_policy=CACHE)
@logged("s3_ingest", max_attempts=4)
def s3_day_task(product: dict, replay_time: datetime | None = None) -> dict:
    from msp.catalog.stac import raster_item
    from msp.quality import checks
    from msp.sources import sentinel3 as s3
    from msp.sources.cdse import CdseSession
    from msp.sources.sentinel2 import write_cog

    cfg = load_config()
    d = product["start"][:10].replace("-", "")
    raw = paths.RAW / "s3" / f"S3_SST_{d}.zip"
    s3.download(product, raw, CdseSession())
    if _incident("corrupt_file") == f"s3:{product['start'][:10]}" and _first_time(f"corrupt_s3_{d}"):
        # Incident 2: the file arrives damaged (simulated by cutting our downloaded copy short)
        raw.write_bytes(raw.read_bytes()[: raw.stat().st_size // 3])
    if not checks.archive_ok(raw):
        # caught before use: quarantine the damaged copy, record it, fetch the file again
        q = paths.RAW / "quarantine" / raw.name
        q.parent.mkdir(parents=True, exist_ok=True)
        raw.replace(q)
        checks.store(f"S3_SST_{d}_download", "sentinel-3-sst",
                     [("archive_integrity", False, "damaged archive quarantined and downloaded again")])
        runlog.record("s3_ingest", "s3_day_task", "recovered", replay_time, 1, 0.0, 0, 0,
                      f"damaged file for {d} caught by the integrity check, quarantined and downloaded again")
        s3.download(product, raw, CdseSession())
    ds = s3.sst_to_grid(raw, cfg.area.bbox, paths.WORK / "s3")
    nc = paths.PRODUCTS / "s3" / f"S3_SST_{d}.nc"
    s3.write_netcdf(ds, nc)
    acq = pd.Timestamp(ds["time"].values[0]).to_pydatetime().replace(tzinfo=UTC)
    tags = {"ACQUISITION_TIME": acq.strftime("%Y-%m-%dT%H:%M:%SZ")}
    cel = (ds["sst"].values[0] - 273.15).astype(np.float32)
    prev = paths.PRODUCTS / "s3" / f"S3_SST_{d}_celsius.tif"
    write_cog(prev, np.where(np.isfinite(cel), cel, -999.0).astype(np.float32), s3.grid_transform(cfg.area.bbox),
              "EPSG:4326", -999.0, tags)
    it = raster_item(f"S3_SST_{d}", prev, acq, "sentinel-3-sst", ITEMS,
                     bands=[{"name": "sst", "unit": "degC"}],
                     extra={"constellation": "sentinel-3", "instruments": ["slstr"], "license": "proprietary",
                            "msp:attribution": "Contains modified Copernicus Sentinel data 2026",
                            "msp:valid_share": round(float(np.isfinite(cel).mean()), 4)})
    import pystac as ps

    from msp.catalog.stac import _rel

    it.add_asset("netcdf", ps.Asset(href=_rel(nc, ITEMS), media_type="application/netcdf", roles=["data"],
                                    title="CF NetCDF (Kelvin)"))
    it.assets["data"].roles = ["visual"]
    _save_item(it)
    res = checks.store(f"S3_SST_{d}", "sentinel-3-sst", checks.check_netcdf(nc, {"sst": (265.0, 300.0)}))
    return {"records": 1, "bytes": nc.stat().st_size, "message": f"QC failed: {res['failed']}"}


@task(retries=3, retry_delay_seconds=[5, 20, 60], cache_policy=CACHE)
@logged("ecmwf_ingest", max_attempts=4)
def ecmwf_run_task(run: datetime, replay_time: datetime | None = None) -> dict:
    from msp.sources import ecmwf

    _simulated_failure("ecmwf", run.strftime("%Y-%m-%dT%H"))
    cfg = load_config()
    res = ecmwf.fetch_run(run, [0, 6], paths.RAW / "ecmwf")
    parts = []
    for r in res:
        ds = ecmwf.to_cf(ecmwf.read_grib(paths.RAW / "ecmwf" / r["file"], cfg.area.bbox))
        parts.append(ds.expand_dims("valid_time") if "valid_time" in ds.coords and "valid_time" not in ds.dims else ds)
    import xarray as xr

    out = paths.PRODUCTS / "ecmwf" / f"ecmwf_ifs_{run:%Y%m%d%H}.nc"
    out.parent.mkdir(parents=True, exist_ok=True)
    merged = xr.concat(parts, dim="valid_time").drop_vars(["step", "time"], errors="ignore")
    merged = merged.rename({"valid_time": "time"})
    merged["time"].attrs.update(standard_name="time", long_name="forecast valid time")
    merged.attrs["forecast_reference_time"] = run.strftime("%Y-%m-%dT%H:%M:%SZ")
    merged.to_netcdf(out)
    return {"records": len(res), "bytes": sum(r["bytes"] for r in res), "message": f"run {run:%Y-%m-%d %H}z"}


# ------------------------------------------------------------------ operations

def _arrived(replay_time: datetime) -> dict[str, tuple[datetime, datetime | None]]:
    """Processed passes whose ingest slot is not later than replay_time: {pass_id: (start, AIS arrival or None)}."""
    from msp.pipeline.s1 import load_scene_list

    done = set(db.query_df("SELECT pass_id FROM passes").pass_id)
    rl = db.query_df("""SELECT replay_time, status, message FROM run_log WHERE flow = 'ais_ingest'
                        AND task = 'ais_task' AND status IN ('failed', 'recovered') AND replay_time <= %s""", (replay_time,))
    # each replay window sees only its own season's passes (the winter sample is not "older summer data")
    cfg = load_config()
    winter_end = datetime.fromisoformat(cfg.winter.end).replace(tzinfo=UTC) + timedelta(days=2)
    return arrival(load_scene_list("winter" if replay_time <= winter_end else "summer"), done, rl, replay_time)


def arrival(scenes: list[dict], done: set[str], rl: pd.DataFrame,
            replay_time: datetime) -> dict[str, tuple[datetime, datetime | None]]:
    """Pure part of `_arrived`: rl holds the AIS ingest rows with status failed or recovered. AIS arrives
    at the pass's ingest slot, or, when the ingest failed there, at the run that filled the gap."""
    rl = rl[rl.replay_time <= pd.Timestamp(replay_time)]
    failed_at = set(rl.loc[rl.status == "failed", "replay_time"])
    fills = rl[rl.status == "recovered"]
    out = {}
    for sc in scenes:
        pid, t_in = sc["pass_id"], ingest_slot(sc)
        if pid in done and t_in <= replay_time:
            if pd.Timestamp(t_in) not in failed_at:
                ais_at = t_in
            else:
                f = fills[fills.message.fillna("").str.contains(pid, regex=False)]
                ais_at = f.replay_time.min().to_pydatetime() if len(f) else None
            out[pid] = (pd.Timestamp(sc["start"]).to_pydatetime(), ais_at)
    return out


@task
def freshness_task(replay_time: datetime, sources: list[str] | None = None) -> list[dict]:
    """Newest data per source as seen on the replay clock; alerts when stale."""
    want = set(sources or ["s1", "s2", "s3", "ecmwf"])
    out = []
    # what had arrived by the replay time: a radar pass from its ingest slot on, its AIS from the same slot,
    # or, when the AIS ingest failed there, from the run that filled the gap (both read from the run log)
    avail = _arrived(replay_time)
    last_id, (last_pass, last_ais) = max(avail.items(), key=lambda kv: kv[1][0]) if avail else (None, (None, None))
    # "s1": radar delivery is monitored; "ais" alone: radar is a chosen sample (winter), only AIS is checked
    if "s1" in want:
        out.append(runlog.check_freshness("sentinel-1", replay_time, last_pass))
    # AIS is needed at each radar pass: checked while the latest pass is recent, and also while its AIS is
    # missing and in the slot where it finally arrives (so an outage alert clears when the gap is filled)
    if want & {"s1", "ais"} and last_pass is not None and (replay_time - last_pass <= timedelta(hours=12) or last_ais is None
                                  or replay_time - last_ais < timedelta(hours=6)):
        have = [pid for pid, (_, ais_at) in avail.items() if ais_at is not None]
        latest = db.query_df("SELECT max(t) FROM ais_positions WHERE pass_id = ANY(%s) AND t <= %s",
                             (have, last_pass + timedelta(minutes=30))).iloc[0, 0] if have else None
        out.append(runlog.check_freshness("ais", replay_time, latest, against=last_pass))
    for src, folder, pat in (("sentinel-2", "s2", "S2_*_cloudmask.tif"), ("sentinel-3", "s3", "S3_SST_*.nc")):
        if src.replace("sentinel-", "s") not in want:
            continue
        times = sorted(datetime.strptime(p.name.split("_")[1 if src == "sentinel-2" else 2][:8], "%Y%m%d")
                       .replace(tzinfo=UTC, hour=11) for p in (paths.PRODUCTS / folder).glob(pat))
        times = [t for t in times if t <= replay_time]
        out.append(runlog.check_freshness(src, replay_time, times[-1] if times else None))
    if "ecmwf" not in want:
        return out
    from msp.sources.ecmwf import PUBLISH_DELAY

    runs = sorted(datetime.strptime(p.stem.split("_")[-1], "%Y%m%d%H").replace(tzinfo=UTC)
                  for p in (paths.PRODUCTS / "ecmwf").glob("ecmwf_ifs_*.nc"))
    # a run counts from the first 6-hourly ingest slot after it is published, as in live operation
    runs = [r for r in runs if slot(r + PUBLISH_DELAY) <= replay_time]
    out.append(runlog.check_freshness("ecmwf-forecast", replay_time, runs[-1] if runs else None))
    return out


# ------------------------------------------------------------------ flows

@flow(name="s1_ingest", log_prints=True)
def s1_ingest_flow(scene: dict, replay_time: datetime | None = None, engine: str = "snap",
                   downloaded: bool = False) -> str:
    if not downloaded:
        s1_download(scene, replay_time=replay_time)
    r = s1_process(scene["pass_id"], engine, replay_time=replay_time)
    s1_product(scene, r["engine"], replay_time=replay_time)
    return r["engine"]


@flow(name="ship_detect", log_prints=True)
def ship_detect_flow(pass_id: str, engine: str, replay_time: datetime | None = None, keep_raw: bool = False) -> None:
    detect_task(pass_id, engine, detector_settings(), replay_time=replay_time)
    cleanup_task(pass_id, engine, keep_raw)


@flow(name="ais_and_match", log_prints=True)
def ais_match_flow(pass_id: str, replay_time: datetime | None = None) -> None:
    s = detector_settings()
    ais_task(pass_id, replay_time=replay_time)
    sea_state_task(pass_id, s, replay_time=replay_time)
    match_task(pass_id, s, replay_time=replay_time)


@flow(name="s2_ingest", log_prints=True)
def s2_ingest_flow(day: str, items: list, replay_time: datetime | None = None) -> None:
    s2_day_task(day, json.dumps([i.to_dict() for i in items]), replay_time=replay_time)


@flow(name="s3_ingest", log_prints=True)
def s3_ingest_flow(product: dict, replay_time: datetime | None = None) -> None:
    s3_day_task(product, replay_time=replay_time)


@flow(name="ecmwf_ingest", log_prints=True)
def ecmwf_ingest_flow(run: datetime, replay_time: datetime | None = None) -> None:
    ecmwf_run_task(run, replay_time=replay_time)


@flow(name="ops_replay", log_prints=True)
def ops_replay_flow(start: str, end: str, sources: str) -> int:
    """Recompute the freshness checks for every 6-hour slot of a replay range (after the ingest replays).
    Uses only what had arrived by each slot (data times, ingest slots and the run log), so it gives the
    same answer as checking live."""
    d0 = datetime.fromisoformat(start).replace(tzinfo=UTC)
    d1 = datetime.fromisoformat(end).replace(tzinfo=UTC)
    n, t = 0, d0
    while t < d1 + timedelta(days=1):
        freshness_task(t, sources.split(","))
        n += 1
        t += timedelta(hours=6)
    return n


@flow(name="ops_check", log_prints=True)
def ops_flow(replay_time: datetime | None = None, sources: list[str] | None = None) -> list[dict]:
    """Freshness checks at the replay clock (or the current time in live mode), for the sources that
    this replay window ingests."""
    return freshness_task(replay_time or datetime.now(UTC), sources)


@flow(name="live_ingest", log_prints=True)
def live_ingest_flow() -> dict:
    """Live mode: run every flow for the last 24 hours (used by the 6-hourly schedule of `msp serve`)."""
    now = datetime.now(UTC)
    return backfill_flow(start=(now - timedelta(days=1)).strftime("%Y-%m-%d"), end=now.strftime("%Y-%m-%d"))


@flow(name="backfill", log_prints=True)
def backfill_flow(start: str | None = None, end: str | None = None, sources: str = "s1,s2,s3,ecmwf,ops",
                  engine: str = "snap") -> dict:
    """Replay the study period day by day through every flow, as if live."""
    from msp.pipeline.s1 import load_scene_list
    from msp.sources import ecmwf
    from msp.sources import sentinel2 as s2

    cfg = load_config()
    p0, p1 = _period()
    d0 = datetime.fromisoformat(start).replace(tzinfo=UTC) if start else p0
    d1 = datetime.fromisoformat(end).replace(tzinfo=UTC) if end else p1
    src = set(sources.split(","))
    scenes = load_scene_list() if "s1" in src else []
    s2_days = s2.group_by_day(s2.search_items(cfg.area.bbox, cfg.period.start, cfg.period.end)) if "s2" in src else {}
    s3_products = json.loads((paths.WORK / "s3_selected.json").read_text()) if "s3" in src else []
    done = {"s1": 0, "s2": 0, "s3": 0, "ecmwf": 0, "ops": 0}
    pending_ais: list[str] = []
    # the winter radar passes are a sample chosen by sea state: there only AIS freshness is monitored
    winter = (datetime.fromisoformat(cfg.winter.start).replace(tzinfo=UTC) <= d0
              and d1 <= datetime.fromisoformat(cfg.winter.end).replace(tzinfo=UTC) + timedelta(days=1))
    ops_sources = sorted((src - {"ops"} - {"s1"}) | {"ais"}) if winter and "s1" in src else sorted(src - {"ops"})
    # the next radar scene downloads while the current one is processed (one ahead, to spare the disk)
    # only scenes whose ingest slot falls in this replay's range (summer and winter are replayed separately)
    todo_s1 = [s for s in sorted(scenes, key=ingest_slot)
               if d0 <= ingest_slot(s) < d1.replace(hour=0, minute=0) + timedelta(days=1) and not _already_processed(s["pass_id"])]
    ahead: dict[str, object] = {}

    def fetch_ahead(after: str | None) -> None:
        i = 0 if after is None else next((k + 1 for k, s in enumerate(todo_s1) if s["pass_id"] == after), len(todo_s1))
        if i < len(todo_s1) and todo_s1[i]["pass_id"] not in ahead:
            nxt = todo_s1[i]
            ahead[nxt["pass_id"]] = s1_download.submit(nxt, replay_time=ingest_slot(nxt))

    if todo_s1:
        fetch_ahead(None)
    day = d0.replace(hour=0, minute=0)
    while day <= d1:
        day.strftime("%Y-%m-%d")
        for hour in (0, 6, 12, 18):
            clock = day.replace(hour=hour)
            if "ecmwf" in src:
                run = ecmwf.latest_published_run(clock)
                if p0 <= run <= p1:
                    ecmwf_ingest_flow(run, replay_time=clock)
                    done["ecmwf"] += 1
            # AIS that could not be fetched during an outage is filled in once the service is back
            if pending_ais and not _ais_outage(clock):
                for pid in pending_ais:
                    ais_match_flow(pid, replay_time=clock)
                    runlog.record("ais_ingest", "ais_task", "recovered", clock, 2, 0.0, 0, 0,
                                  f"gap filled after the outage: AIS for {pid}")
                pending_ais.clear()
            for sc in [s for s in scenes if ingest_slot(s) == clock]:
                pid = sc["pass_id"]
                if _already_processed(pid):
                    eng = db.query_df("SELECT engine FROM passes WHERE pass_id = %s", (pid,)).engine.iloc[0]
                else:
                    fut = ahead.pop(pid, None)
                    if fut is not None:
                        fut.result()
                    fetch_ahead(pid)
                    eng = s1_ingest_flow(sc, replay_time=clock, engine=engine, downloaded=fut is not None)
                    ship_detect_flow(pid, eng, replay_time=clock, keep_raw=pid == "S1D_20260806T055526_R110")
                if _ais_outage(clock):
                    runlog.record("ais_ingest", "ais_task", "failed", clock, 1, 0.0, 0, 0,
                                  "AIS service unavailable (outage); the pass waits for the gap fill")
                    pending_ais.append(pid)
                else:
                    ais_match_flow(pid, replay_time=clock)
                done["s1"] += 1
            for d2, items in s2_days.items():
                if slot(s2.acquisition_time(items)) == clock:
                    s2_ingest_flow(d2, items, replay_time=clock)
                    done["s2"] += 1
            for pr in [p for p in s3_products if slot(p["start"]) == clock]:
                s3_ingest_flow(pr, replay_time=clock)
                done["s3"] += 1
            if "ops" in src:
                # freshness is checked right after the slot's ingest, as a scheduled deployment would
                ops_flow(clock, ops_sources)
                done["ops"] += 1
        day += timedelta(days=1)
    return done


__all__ = ["backfill_flow", "s1_ingest_flow", "ship_detect_flow", "ais_match_flow", "s2_ingest_flow",
           "s3_ingest_flow", "ecmwf_ingest_flow", "ops_flow", "time", "Path"]


@flow(name="snap_prefetch", log_prints=True)
def snap_prefetch_flow(max_ahead: int = 2) -> int:
    """Run SNAP for upcoming passes while the backfill works on detection and matching.

    Stays at most `max_ahead` working rasters ahead (disk space); the "snap" concurrency limit keeps it
    to one SNAP run at a time across all flows.
    """
    import time as _time

    from msp.pipeline.s1 import load_scene_list, product_cog, work_raster

    done = 0
    for sc in load_scene_list():
        pid = sc["pass_id"]
        if product_cog(pid).exists() or work_raster(pid, "snap").exists():
            continue
        while len(list((paths.WORK / "s1").glob("*_snap.tif"))) >= max_ahead:
            _time.sleep(30)
        if product_cog(pid).exists():
            continue
        s1_download(sc, replay_time=ingest_slot(sc))
        s1_process(pid, "snap", replay_time=ingest_slot(sc))
        done += 1
    return done


def refresh_visibility(pass_id: str) -> None:
    """Recompute the imaged sea area (buffered mask) and which AIS ships fall inside it."""
    from msp.pipeline.s1 import product_cog, valid_sea_area

    area = valid_sea_area(product_cog(pass_id), LAND_BUFFERED, STRUCTURES)
    db.execute("UPDATE passes SET valid_area = ST_Multi(ST_GeomFromText(%s, 32632)) WHERE pass_id = %s",
               (area.wkt, pass_id))
    db.execute("""UPDATE ais_at_pass a SET in_scene = ST_Within(a.geom, p.valid_area)
                  FROM passes p WHERE p.pass_id = a.pass_id AND a.pass_id = %s""", (pass_id,))


@flow(name="redetect", log_prints=True)
def redetect_flow(pass_ids: list[str] | None = None) -> dict:
    """Frozen detector settings applied to all passes (or the given ones), then sea state and matching again."""
    from msp.pipeline.s1 import load_scene_list

    s = detector_settings()
    n = 0
    for sc in load_scene_list():
        pid = sc["pass_id"]
        if pass_ids and pid not in pass_ids:
            continue
        if not db.query_df("SELECT 1 FROM passes WHERE pass_id = %s", (pid,)).shape[0]:
            continue
        clock = ingest_slot(sc)
        eng = db.query_df("SELECT engine FROM passes WHERE pass_id = %s", (pid,)).engine.iloc[0]
        refresh_visibility(pid)
        detect_task(pid, eng, s, replay_time=clock)
        sea_state_task(pid, s, replay_time=clock)
        match_task(pid, s, replay_time=clock)
        n += 1
    return {"passes": n, "settings": s}


def _mask_fingerprint() -> str:
    import hashlib

    h = hashlib.sha256()
    for f in sorted(LAND_BUFFERED.parent.glob("land_buffered.*")) + [STRUCTURES]:
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


@flow(name="s1_reprocess", log_prints=True)
def s1_reprocess_flow(pass_ids: list[str] | None = None) -> dict:
    """First detector stage again from the full-resolution images, after the land mask changed.

    The 10 m working rasters are deleted once a pass is done, so each scene is downloaded and run
    through SNAP again; the next download runs while the current scene is processed. Products and pass
    records are left as they are. A marker per pass records the mask it was detected with, so an
    interrupted run resumes where it stopped. Run `redetect` afterwards for the final detections.
    """
    from msp.detect.cfar import DetectorParams
    from msp.pipeline.s1 import candidates_path, detect_pass, load_scene_list

    params = DetectorParams.from_config(detector_settings())
    mask = _mask_fingerprint()
    keep = "S1D_20260806T055526_R110"  # raw scene kept for the SNAP versus Python comparison
    todo = [sc for sc in load_scene_list() if (not pass_ids or sc["pass_id"] in pass_ids)
            and not (candidates_path(sc["pass_id"]).with_suffix(".mask").exists()
                     and candidates_path(sc["pass_id"]).with_suffix(".mask").read_text() == mask)]
    dl = s1_download.with_options(refresh_cache=True)
    proc = s1_process.with_options(refresh_cache=True)
    nxt = dl.submit(todo[0], replay_time=ingest_slot(todo[0])) if todo else None
    for k, sc in enumerate(todo):
        pid, clock = sc["pass_id"], ingest_slot(sc)
        nxt.result()
        nxt = dl.submit(todo[k + 1], replay_time=ingest_slot(todo[k + 1])) if k + 1 < len(todo) else None
        eng = proc(pid, "snap", replay_time=clock)["engine"]
        detect_pass(pid, eng, LAND_BUFFERED, STRUCTURES, params)
        cleanup_task(pid, eng, keep_raw=pid == keep)
        candidates_path(pid).with_suffix(".mask").write_text(mask)
        print(f"{pid}: stage 1 done ({k + 1} of {len(todo)})")
    return {"passes": len(todo), "mask": mask}


@task(cache_policy=CACHE)
@logged("ship_classify")
def classify_task(model_version: str, replay_time: datetime | None = None) -> dict:
    from msp.ml.train import classify_all

    return classify_all()


@flow(name="ship_classify", log_prints=True)
def classify_flow() -> dict:
    """Run the trained model on every detection; adds ship probability and estimated length."""
    import hashlib

    from msp.ml.train import MODEL_DIR

    version = hashlib.sha256((MODEL_DIR / "shipnet.pt").read_bytes()).hexdigest()[:12]
    return classify_task(version + json.dumps(detector_settings(), sort_keys=True))
