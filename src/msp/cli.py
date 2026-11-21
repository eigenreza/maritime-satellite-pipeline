"""Command line: `msp <command>`. Every pipeline stage, the backfill and the demo export run from here."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from msp import paths
from msp.config import load_config

app = typer.Typer(help="Maritime satellite pipeline: radar ships, AIS and sea state off western Norway.",
                  no_args_is_help=True, add_completion=False)
ml_app = typer.Typer(help="Ship classifier: cut-outs, training, calibration, evaluation.", no_args_is_help=True)
app.add_typer(ml_app, name="ml")


def _bbox() -> list[float]:
    return list(load_config().area.bbox)


@app.command("init-db")
def init_db() -> None:
    """Create the PostGIS tables."""
    from msp.db import init_schema

    init_schema()
    typer.echo("schema ready")


@app.command("prefect-setup")
def prefect_setup() -> None:
    """Create the concurrency limits used by the flows (SNAP: 1 run, CDSE downloads: 2)."""
    import asyncio

    from prefect.client.orchestration import get_client

    async def go():
        async with get_client() as c:
            for tag, n in (("snap", 1), ("cdse", 2)):
                await c.create_concurrency_limit(tag=tag, concurrency_limit=n)
    asyncio.run(go())
    typer.echo("concurrency limits: snap=1, cdse=2")


@app.command("s1-select")
def s1_select() -> None:
    """Search the Copernicus catalogue: Sentinel-1 candidate passes and the Sentinel-3 SST products."""
    import dataclasses

    from msp.sources.cdse import search_s1_grd, select_passes

    cfg = load_config()
    sc = [s for s in search_s1_grd(tuple(cfg.area.bbox), cfg.period.start, cfg.period.end)
          if s.start[:10] <= cfg.period.end]
    sel = select_passes(sc, list(cfg.sentinel1.candidate_orbits), float(cfg.sentinel1.min_area_coverage))
    out = paths.WORK / "s1_candidates.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([dataclasses.asdict(s) for s in sel], indent=1))
    typer.echo(f"{len(sel)} candidate passes -> {paths.rel(out)} (passes used: config/s1_passes.txt)")
    from msp.sources.sentinel3 import pick_daily, search_wst

    s3 = [p for p in search_wst(list(cfg.area.bbox), cfg.period.start, cfg.period.end) if p["start"][:10] <= cfg.period.end]
    s3sel = pick_daily(s3, every_n_days=2)
    (paths.WORK / "s3_selected.json").write_text(json.dumps(s3sel, indent=1))
    typer.echo(f"{len(s3sel)} Sentinel-3 SST products selected (one every second day)")


@app.command("winter-select")
def winter_select() -> None:
    """Choose the winter passes by sea state (before any winter radar is downloaded); writes
    config/s1_passes_winter.txt and the sea-state table for every candidate."""
    from msp.config import ROOT
    from msp.pipeline import winter

    cands = winter.candidates_json()
    df = winter.sea_state_at_passes(cands, paths.PRODUCTS / "metocean" / "metno_waves_wind_winter.nc",
                                    paths.PRODUCTS / "landmask" / "land.shp")
    starts = dict(zip(df.pass_id, df.start, strict=True))
    checked = {p: winter.ais_check(p, starts[p]) for p in df.pass_id}  # every candidate, small requests
    df["ais_ok"] = df.pass_id.map(checked)
    sel, dropped = winter.select(df, lambda p: checked[p])
    df.round(3).to_csv(paths.WORK / "winter_candidates_sea_state.csv", index=False)
    winter.write_pass_list(df, sel, dropped, ROOT / "config" / "s1_passes_winter.txt")
    typer.echo(df.groupby("stratum").size().to_dict())
    typer.echo(f"{len(sel)} winter passes selected, {sum(df[df.pass_id.isin(sel)].size_bytes) / 1e9:.1f} GB")


@app.command("reference-layers")
def reference_layers() -> None:
    """Land mask Shapefile (Kartverket N50) and offshore structures (GeoParquet)."""
    from msp.sources.landmask import build_land_mask, fetch_structures

    cfg = load_config()
    z = paths.RAW / "landmask" / "n50_vestland.zip"
    typer.echo(build_land_mask(z, _bbox(), cfg.area.crs, paths.PRODUCTS / "landmask", float(cfg.landmask.buffer_m)))
    typer.echo(fetch_structures(_bbox(), cfg.area.crs, paths.PRODUCTS / "structures" / "structures.parquet",
                                paths.PRODUCTS / "landmask" / "land.shp"))


@app.command("metocean")
def metocean() -> None:
    """MET Norway wave model fields (NetCDF over OPeNDAP) for the whole period."""
    import xarray as xr

    from msp.sources.metno import fetch_waves, write_cf

    cfg = load_config()
    out = paths.PRODUCTS / "metocean" / "metno_waves_wind.nc"
    parts = []
    for name, sec in (("summer", cfg.period), ("winter", cfg.winter)):
        f = out.with_name(f"metno_waves_wind_{name}.nc")
        typer.echo(fetch_waves(_bbox(), date.fromisoformat(sec.start), date.fromisoformat(sec.end), f))
        parts.append(f)
    # both seasons in one CF file (same model grid), so every sampler finds the right hour
    ds = xr.concat([xr.load_dataset(p) for p in parts], dim="time").sortby("time")
    raw = ds.rename({"wave_dir": "thq", "wind_speed": "ff", "wind_dir": "dd"})
    raw["thq"] = (raw["thq"] + 180.0) % 360.0
    raw["dd"] = (raw["dd"] + 180.0) % 360.0
    write_cf(raw[["hs", "thq", "ff", "dd"]], ds["lat"].values, ds["lon"].values, out)
    typer.echo({"file": out.name, "bytes": out.stat().st_size, "times": int(ds.sizes["time"])})


@app.command("backfill")
def backfill(start: str = typer.Option(None, help="first day (YYYY-MM-DD), default: period start"),
             end: str = typer.Option(None, help="last day, default: period end"),
             sources: str = typer.Option("s1,s2,s3,ecmwf,ops", help="comma-separated sources"),
             engine: str = typer.Option("snap", help="snap or python")) -> None:
    """Replay the study period day by day through all flows (Prefect)."""
    from msp.flows import backfill_flow

    typer.echo(backfill_flow(start=start, end=end, sources=sources, engine=engine))


@app.command("ops-replay")
def ops_replay(start: str = typer.Option(..., help="first day (YYYY-MM-DD)"), end: str = typer.Option(..., help="last day"),
               sources: str = typer.Option("s1,s2,s3,ecmwf", help="sources ingested in this range")) -> None:
    """Recompute the freshness checks and alerts for every slot of a replay range."""
    from msp.flows import ops_replay_flow

    typer.echo(ops_replay_flow(start, end, sources))


@app.command("process-pass")
def process_pass(pass_id: str, engine: str = "snap") -> None:
    """Run one Sentinel-1 pass through ingest, detection, AIS and matching."""
    from msp.flows import ais_match_flow, s1_ingest_flow, ship_detect_flow
    from msp.pipeline.s1 import load_scene_list

    sc = next(s for s in load_scene_list() if s["pass_id"] == pass_id)
    eng = s1_ingest_flow(sc, engine=engine)
    ship_detect_flow(pass_id, eng)
    ais_match_flow(pass_id)


@app.command("quality")
def quality() -> None:
    """Re-run the quality checks for every product on disk."""
    from msp.quality.checks import run_all

    typer.echo(run_all())


@app.command("catalogue")
def catalogue() -> None:
    """Build the STAC catalogue from all item files, validate it and write the GeoParquet index."""
    from msp.catalog.build import build_all

    typer.echo(build_all())


@app.command("serve")
def serve() -> None:
    """Serve the flows with round-the-clock schedules (live mode): ingest every 6 hours, checks every hour."""
    from prefect import serve as pserve

    from msp.flows import live_ingest_flow, ops_flow

    checks = ops_flow.to_deployment(name="ops-hourly", cron="5 * * * *")
    ingest = live_ingest_flow.to_deployment(name="ingest-every-6h", cron="30 */6 * * *")
    pserve(checks, ingest)


@app.command("sar-compare")
def sar_compare(pass_id: str = "S1D_20260806T055526_R110") -> None:
    """Run SNAP and the pure-Python fallback on the same scene, time both, compare sigma0 over sea."""
    import time

    from msp.pipeline.s1 import work_raster
    from msp.sar import compare, fallback, snap

    safe = paths.RAW / "s1" / f"{pass_id}.zip"
    t0 = time.time()
    snap.run_snap(safe, work_raster(pass_id, "snap"))
    t1 = time.time()
    fallback.process_scene(safe, work_raster(pass_id, "python"))
    t2 = time.time()
    typer.echo(compare.run(pass_id, round(t1 - t0, 1), round(t2 - t1, 1)))
    for eng in ("snap", "python"):
        work_raster(pass_id, eng).unlink(missing_ok=True)


@app.command("tune-detector")
def tune_detector() -> None:
    """Fix the detector thresholds on the development passes (writes them to the config)."""
    from msp.detect.tune import run

    typer.echo(run())


@app.command("line-rule")
def line_rule() -> None:
    """Fix the line-shaped rule on the summer development passes (writes it to the config)."""
    import re

    from msp import db
    from msp.config import ROOT
    from msp.detect.shape import MAX_SHIP_LENGTH_M, fit_ratio
    from msp.pipeline.s1 import pass_splits

    sp = pass_splits()
    dev = list(sp.loc[sp.research_split == "dev", "pass_id"])
    d = db.query_df("""SELECT d.length_m, d.width_m, (m.vessel_hash IS NOT NULL) AS matched
                       FROM detections d LEFT JOIN matches m USING (det_id) WHERE d.pass_id = ANY(%s)""", (dev,))
    ratio = fit_ratio(d)
    p = ROOT / "config" / "pipeline.yaml"
    text = p.read_text(encoding="utf-8")
    line = f"  line_max_elongation: {ratio}   # fixed by `msp line-rule` on the development passes (frozen)"
    if "line_max_elongation" in text:
        text = re.sub(r"^  line_max_elongation:.*$", line, text, flags=re.M)
    else:
        text = text.replace("  min_peak_db:", line + "\n  min_peak_db:", 1)
    p.write_text(text, encoding="utf-8")
    load_config.cache_clear()
    typer.echo({"dev_detections": len(d), "matched": int(d.matched.sum()), "max_elongation": ratio,
                "max_ship_length_m": MAX_SHIP_LENGTH_M})


@app.command("redetect")
def redetect(pass_id: list[str] = typer.Option(None, help="only these passes (default: all processed)")) -> None:
    """Apply the frozen detector settings to every pass and redo matching (uses stored candidates)."""
    from msp.flows import redetect_flow

    typer.echo(redetect_flow(pass_id or None))


@app.command("reprocess")
def reprocess(pass_id: list[str] = typer.Option(None, help="only these passes (default: all)")) -> None:
    """Download and process the radar scenes again and redo the first detector stage (after a mask change)."""
    from msp.flows import s1_reprocess_flow

    typer.echo(s1_reprocess_flow(pass_id or None))


@app.command("evaluate")
def evaluate() -> None:
    """Research results with bootstrap intervals (results/*.csv, results/research_summary.json)."""
    from msp.evaluate.research import run

    typer.echo(json.dumps(run(), indent=1, default=str))


@ml_app.command("train")
def ml_train(epochs: int = typer.Option(60, help="training epochs for the CNN (best epoch on calibration passes is kept)")) -> None:
    """Train the baseline and the CNN on training passes, calibrate, evaluate on test passes."""
    from msp.ml.train import run

    r = run(epochs=epochs)
    typer.echo(json.dumps({k: v for k, v in r.items() if k != "scores"}, indent=1, default=float))


@ml_app.command("evaluate")
def ml_evaluate() -> None:
    """Score the committed models, unchanged, on the summer test passes and the winter passes."""
    from msp.ml.train import evaluate_frozen

    typer.echo(json.dumps(evaluate_frozen(), indent=1))


@ml_app.command("classify")
def ml_classify() -> None:
    """ship_classify: add ship probability and estimated length to every detection (PostGIS, GeoParquet)."""
    from msp.flows import classify_flow

    typer.echo(classify_flow())


@app.command("demo-export")
def demo_export(images: bool = typer.Option(True, help="re-render image overlays")) -> None:
    """Precompute everything the website shows into web/public/data."""
    from msp.demo.export import run

    typer.echo(json.dumps(run(images=images), indent=1, default=str))


@app.command("readme-tables")
def readme_tables(check: bool = typer.Option(False, help="only check that README tables match results/")) -> None:
    """Write (or check) the generated result tables in README.md."""
    from msp.report import readme

    ok = readme.check() if check else readme.write()
    if not ok:
        raise typer.Exit(1)


@app.command("figures")
def figures() -> None:
    """Result figures for the README (PNG without timestamps)."""
    from msp.report import figures as figs

    typer.echo(figs.run())


@app.command("scan")
def scan() -> None:
    """Hygiene and privacy scan of every public file."""
    from msp.report import scans

    problems = scans.run()
    for p in problems:
        typer.echo(p)
    if problems:
        raise typer.Exit(1)
    typer.echo("clean")


if __name__ == "__main__":
    app()


__all__ = ["app", "Path"]
