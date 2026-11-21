# Pipeline and orchestration

All flows and tasks live in `src/msp/flows.py` and run on Prefect 3, a Python workflow engine. A
flow is a function that calls tasks, and a task is one step with its own retries, cache and run
history. The command line in `src/msp/cli.py` starts the flows.

Terms: SAFE is the zipped Sentinel-1 product folder. SNAP is ESA's toolbox for Sentinel data. COG,
cloud-optimized GeoTIFF: an image file that can be read in pieces over the web. STAC: a standard
catalog format for satellite data. CFAR, constant false alarm rate: compares each pixel with its local
sea background. AIS: the radio system in which ships report their position.

## Tasks

| Task | Run log flow name | Retries | Retry delays (s) | Tag | Cached |
|---|---|---|---|---|---|
| `s1_download` | `s1_ingest` | 3 | 10, 30, 90 | `cdse` | yes |
| `s1_process` | `s1_ingest` | 1 | 30 | `snap` | yes |
| `s1_product` | `s1_ingest` | 0 | | | yes |
| `detect_task` | `ship_detect` | 0 | | | yes |
| `cleanup_task` | not logged | 0 | | | no |
| `ais_task` | `ais_ingest` | 3 | 10, 30, 90 | | yes |
| `sea_state_task` | `metocean_ingest` | 0 | | | yes |
| `match_task` | `match` | 0 | | | yes |
| `s2_day_task` | `s2_ingest` | 2 | 15, 60 | | yes |
| `s3_day_task` | `s3_ingest` | 3 | 10, 30, 90 | `cdse` | yes |
| `ecmwf_run_task` | `ecmwf_ingest` | 3 | 5, 20, 60 | | yes |
| `freshness_task` | not logged (writes `freshness`) | 0 | | | no |
| `classify_task` | `ship_classify` | 0 | | | yes |

What the tasks do:

- `s1_download` streams the SAFE zip from Copernicus Data Space and checks its MD5 sum
  (`CdseSession.download` in `src/msp/sources/cdse.py`). A complete file is not downloaded again.
- `s1_process` runs the SNAP graph. If SNAP fails, it raises an alert and runs the pure-Python chain
  for the same pass. It returns the engine that succeeded.
- `s1_product` writes the 20 m dB COG, the `passes` row with the footprint and the imaged sea area,
  the STAC item and the quality checks.
- `detect_task` runs CFAR once per pass (stored candidates), then applies the second-stage thresholds
  and replaces the pass's rows in `detections`.
- `cleanup_task` deletes the 10 m working raster, and the raw SAFE unless `keep_raw` is set, once the
  product COG and the candidates exist.
- `ais_task` fetches AIS around the pass and stores hashed positions and ship static data.
  `sea_state_task` samples wind and waves at every detection and AIS ship. `match_task` matches them.
- `s2_day_task`, `s3_day_task` and `ecmwf_run_task` build the other sources' products.

## Caching

Cached tasks use `cache_policy=INPUTS + TASK_SOURCE`: the cache key combines the task's arguments and
its source code. Calling a finished task again with the same arguments returns the stored result
without running it, and editing the task's code invalidates its cache. The arguments include
`replay_time` and, for detection and matching, the detector settings, so new settings produce new
runs. A cached call does not write a `run_log` row, because the wrapped function does not run.

## Concurrency limits

Tasks carry tags, and Prefect limits how many tagged tasks run at once. Tag `snap` allows 1 run,
because SNAP needs several GB of memory. Tag `cdse` allows 2, to keep parallel downloads from one
Copernicus Data Space account low. `msp prefect-setup` creates both limits on the Prefect server.
Run it once after the stack starts.

## Replay clock and `slot()`

The pipeline replays the study period as if it were running live round the clock. Every task
receives `replay_time`, a time inside the period, and `run_log` stores that time instead of the wall
clock. `slot(t)` returns the next 6-hour boundary after `t`, so data acquired at 05:55 UTC is processed
in the 06:00 slot and data acquired at 06:10 UTC in the 12:00 slot.

A radar scene is not available the moment it is recorded: the provider publishes it some hours later.
`ingest_slot(scene)` therefore uses the later of the acquisition start and the Copernicus Data Space
publication time (`PublicationDate` in the catalogue), so a scene recorded at 05:55 UTC and published at
07:10 UTC is fetched in the 12:00 slot, as a live deployment would. The two study periods (summer and
winter) are replayed separately: `msp backfill` for summer, and
`msp backfill --start 2025-12-01 --end 2026-03-15 --sources s1,ops` for winter.

## Flows

| Flow | Function | What it runs |
|---|---|---|
| `s1_ingest` | `s1_ingest_flow(scene, replay_time, engine)` | `s1_download`, `s1_process`, `s1_product` |
| `ship_detect` | `ship_detect_flow(pass_id, engine, replay_time, keep_raw)` | `detect_task`, `cleanup_task` |
| `ais_and_match` | `ais_match_flow(pass_id, replay_time)` | `ais_task`, `sea_state_task`, `match_task` |
| `s2_ingest` | `s2_ingest_flow(day, items, replay_time)` | `s2_day_task` |
| `s3_ingest` | `s3_ingest_flow(product, replay_time)` | `s3_day_task` |
| `ecmwf_ingest` | `ecmwf_ingest_flow(run, replay_time)` | `ecmwf_run_task` |
| `ops_check` | `ops_flow(replay_time)` | `freshness_task` |
| `backfill` | `backfill_flow(start, end, sources, engine)` | all of the above, day by day |
| `snap_prefetch` | `snap_prefetch_flow(max_ahead)` | `s1_download`, `s1_process` ahead of the backfill |
| `redetect` | `redetect_flow()` | `detect_task`, `sea_state_task`, `match_task` for every stored pass |
| `ship_classify` | `classify_flow()` | `classify_task` |

### Backfill

`backfill_flow` walks from `start` to `end` (default: the whole period) one day at a time, and within
each day through the slots 00, 06, 12 and 18 UTC. For each slot it:

1. ingests the newest ECMWF run that would have been published at that time
   (`latest_published_run`, publication 8 hours after the nominal run time),
2. runs `s1_ingest`, `ship_detect` and `ais_and_match` for every selected Sentinel-1 pass whose
   `slot()` equals the slot,
3. runs `s2_ingest` for a Sentinel-2 day whose earliest acquisition falls into the slot,
4. runs `s3_ingest` for a selected Sentinel-3 product whose start falls into the slot,
5. runs `ops_check` at the end of the slot (slot start plus 5 h 59 min).

`sources` limits the replay to a comma-separated subset of `s1,s2,s3,ecmwf,ops`. The Sentinel-1
passes come from `load_scene_list()` in `src/msp/pipeline/s1.py`: the IDs in `config/s1_passes.txt`,
looked up in `data/work/s1_candidates.json`. Sentinel-3 products come from
`data/work/s3_selected.json`. One pass is processed with `keep_raw=True`, so its SAFE stays on disk.

### SNAP prefetch

`snap_prefetch_flow` runs SNAP for upcoming passes while the backfill works on detection and
matching. It stays at most `max_ahead` working rasters ahead (default 2) to bound disk use, and polls
every 30 seconds. The `snap` limit keeps it to one SNAP run across all flows. It has no CLI command:
start it from Python in a second process with `from msp.flows import snap_prefetch_flow`.

### Redetect and ship_classify

`redetect_flow` applies the current detector settings to every pass in `passes`. It reuses the stored
CFAR candidates, so it needs neither raw scenes nor working rasters. `classify_flow` hashes
`models/shipnet.pt` (first 12 hex characters of SHA-256) and passes it as the model version to
`classify_task`, which calls `classify_all()` in `src/msp/ml/train.py`.

## Run log decorator

`logged(flow, max_attempts)` in `src/msp/ops/runlog.py` wraps a task function and writes one
`run_log` row per attempt: flow name, task name, `replay_time`, status, attempt number, run time,
records, bytes and a message. The attempt number comes from Prefect's `task_run.run_count`. The
status is `completed` on a first-attempt success and `recovered` on a later success. A failure is
`retried` while attempts remain and `failed` on the last one. A failing database write only logs a
warning, so the run log never stops the pipeline.

## Simulated failure

`_simulated_failure(source, key)` breaks the first ECMWF download on purpose to show retry and
recovery in the run history. Set `MSP_SIMULATE_FAILURE="ecmwf:<YYYY-MM-DDTHH>"` with a run time inside
the period. The first attempt raises `ConnectionError` and writes a flag file to `data/work/ops/`. The
retry then succeeds and the run log shows `retried` followed by `recovered`.

## Schedules

`msp serve` serves two deployments with Prefect's `serve`: `ops-hourly` runs `ops_flow` on cron
`5 * * * *`, and `daily-ingest` runs `backfill_flow` for all sources on cron `30 */6 * * *`.

## Command line

| Command | Purpose |
|---|---|
| `msp init-db` | Create the PostGIS tables |
| `msp prefect-setup` | Create the `snap` and `cdse` concurrency limits |
| `msp s1-select` | Search Sentinel-1 candidate passes and select Sentinel-3 products |
| `msp reference-layers` | Build the land mask Shapefiles and the structures GeoParquet |
| `msp metocean` | Download MET Norway waves and wind for the period |
| `msp backfill --start --end --sources --engine` | Replay a date range through all flows |
| `msp process-pass <pass_id> --engine` | Run one pass through ingest, detection, AIS and matching |
| `msp tune-detector` | Fix the second-stage thresholds on the development passes |
| `msp reprocess` | Download and process every scene again and redo the first detector stage (after a land mask change) |
| `msp redetect` | Apply the frozen detector settings to every pass |
| `msp catalogue` | Build, validate and index the STAC catalog |
| `msp evaluate` | Write the research results with bootstrap intervals |
| `msp ml train --epochs` | Train, calibrate and evaluate the ship classifier |
| `msp ml classify` | Add ship probability and estimated length to every detection |
| `msp demo-export --images` | Precompute the demo data |
| `msp readme-tables --check` | Write or check the README result tables |
| `msp figures` | Draw the result figures |
| `msp scan` | Hygiene and privacy scan of every public file |
| `msp serve` | Serve the scheduled deployments |
