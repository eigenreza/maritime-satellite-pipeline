# Operations

How the pipeline watches its own data and what to do when something breaks. Flows run on Prefect, a
Python workflow engine with retries and a run history. All times are on the replay clock: the
simulated "now" inside the study period that the backfill moves forward in 6-hour slots (see
`docs/pipeline.md`). SNAP is ESA's toolbox for Sentinel data, and AIS the radio system in which ships
report their position.

## Freshness rules

`FRESHNESS_LIMIT_H` in `src/msp/ops/runlog.py` sets, per source, how old the newest data may be:

| Source | Limit (hours) | Why this limit fits how the source arrives |
|---|---|---|
| `sentinel-1` | 132 | The selected summer passes repeat about weekly on each orbit, with gaps of up to 5 days; an alert means a pass is clearly missing. In winter the radar passes are a sample chosen by sea state, so radar freshness is not monitored there (only AIS is) |
| `sentinel-2` | 96 | The optical satellites pass every two to three days; cloudy days still deliver a file |
| `sentinel-3` | 48 | Sea surface temperature is taken every second day |
| `ecmwf-forecast` | 30 | Two runs a day, published about 8 hours after the run time and fetched in the next 6-hour slot, so the newest fetched run is normally up to 24 hours old |
| `metno-waves` | 6 | Hourly model fields |
| `ais` | 1 | AIS is needed at each radar pass: the newest position must be within an hour of the pass time |

`freshness_task` in `src/msp/flows.py` runs right after each 6-hour slot's ingest, as a scheduled
deployment would, and finds the newest data per source that had arrived by then:

- Sentinel-1: the latest processed pass whose ingest slot (the first slot after the provider published
  it) is not later than the replay clock.
- AIS: latest position in `ais_positions` up to 30 minutes after the latest radar pass, measured
  against that pass time, counting a pass's positions only from when they arrived: its ingest slot, or,
  when the AIS ingest failed there, the run that filled the gap (both from the run log). AIS is fetched
  only around radar passes, so the check runs while the latest pass is less than 12 hours old.

Because every check uses only what had arrived by its slot, the checks can be recomputed after a replay
with `msp ops-replay --start ... --end ... --sources ...` and give the same answer as checking live. Summer: `--sources s1,s2,s3,ecmwf`; winter: `--sources ais`.
- Sentinel-2 and Sentinel-3: the date in the product file names, taken as 11:00 UTC.
- ECMWF: the run time in the NetCDF file names, counted only once the run would have been published
  (8 hours after the nominal run time, `PUBLISH_DELAY` in `src/msp/sources/ecmwf.py`).

`check_freshness` compares the age with the limit and stores `fresh`, `stale` or `missing` in the
`freshness` table, one row per source and replay time. `metno-waves` has a limit but no check yet,
because the wave fields are downloaded once for the whole period by `msp metocean`.

## Alerts

`alert(source, message, replay_time)` writes a warning to the `msp` logger (`ALERT <source>: ...`)
and a `run_log` row with flow `ops`, task `alert:<source>` and status `alert`. Alerts are raised when
a source is stale or missing, and when SNAP or the Python fallback fails for a pass.

## Quality checks

`src/msp/quality/checks.py` checks each product as it is written and stores one row per check in
`quality_checks`. A failed check does not stop the pipeline. The failed check names appear in the task's
run log message.

`check_cog` (raster products):

| Check | Passes when |
|---|---|
| `valid_cog` | `rio-cogeo` validates the file as a COG (cloud-optimized GeoTIFF: an image file that can be read in pieces over the web) |
| `crs` | The file's CRS equals the expected one |
| `bounds_cover_area` | The file covers more than 95% of the study box |
| `nodata_share` | The nodata share is at most the product's limit |
| `value_range` | The 1st and 99th percentile, times the scale, lie inside the expected range |
| `acquisition_time` | The `ACQUISITION_TIME` tag exists |

`check_netcdf` (NetCDF products): `opens`, `cf_conventions` (the `Conventions` attribute starts with
`CF`), `acquisition_time` (a non-empty `time` coordinate), and per variable `standard_name_<var>` and
`range_<var>`.

| Product | Function | Settings |
|---|---|---|
| Sentinel-1 sigma0 COG | `check_cog` | EPSG:32632, range -45 to 25 dB, nodata at most 60%, scale 0.01 |
| Sentinel-2 cloud mask COG | `check_cog` | EPSG:32632, range 0 to 255, nodata not limited |
| Sentinel-3 SST NetCDF | `check_netcdf` | `sst` between 265 and 300 K |

## Tables

| Table | One row per | Key columns |
|---|---|---|
| `run_log` | Task attempt or alert | `flow`, `task`, `replay_time`, `status`, `attempt`, `duration_s`, `records`, `bytes`, `message` |
| `freshness` | Source and replay time | `latest_data`, `age_hours`, `limit_hours`, `status` |
| `quality_checks` | Product and check | `product_type`, `check_name`, `passed`, `value` |

`run_log.status` is one of `completed`, `retried`, `failed`, `recovered` or `alert`. Useful queries:

```sql
-- attempts per flow and status
SELECT flow, status, count(*) FROM run_log GROUP BY flow, status ORDER BY flow, status

-- stale or missing sources
SELECT source, replay_time, age_hours FROM freshness WHERE status <> 'fresh' ORDER BY replay_time

-- failed quality checks
SELECT product_id, check_name, value FROM quality_checks WHERE NOT passed
```

## Operations page in the demo

`export_ops` in `src/msp/demo/export.py` writes `web/public/data/ops.json` for the demo's operations
page:

- per flow: runs, completed, retried, failed and recovered attempts, run time, records and gigabytes,
- per source: the freshness limit, counts of fresh, stale and missing checks, and a timeline,
- the alert count and the 12 most recent alerts,
- quality checks per product type with up to 8 failures,
- the three incidents as the run log recorded them,
- alert episodes per source: when each was raised and when it cleared,
- delivery time per source (below),
- pipeline facts, also written to `results/pipeline_facts.json` and shown in the README `pipeline` table.

## Delivery time

`_delivery` in `src/msp/demo/export.py` measures, on the replay clock, how fast data becomes a product:

- **Sentinel-1:** from the end of the acquisition (`ContentDate.End` in the Copernicus Data Space
  catalogue) to its publication (`PublicationDate`), then from publication to the finished detection
  product. A scene is fetched in the first 6-hour slot after it is published (`ingest_slot` in
  `src/msp/flows.py`), and the measured run time of ingest, detection, sea state and matching is added.
- **AIS:** from the moment its radar pass reaches the pipeline (the pass's ingest slot) to stored,
  matched positions, plus the run time. AIS is fetched for each pass, so a late-published radar scene
  delays its AIS too; that delay is shown on the radar row, not counted again as AIS.
- **ECMWF forecasts:** from the published run (8 hours after the nominal run time) to stored fields.

Median and slowest per source and season are on the operations page. Most of the time to product is
the wait for the next 6-hour slot; processing takes minutes.

## Incidents in the replay

Three incidents are switched on in `config/pipeline.yaml` under `ops.incidents`, not by editing data.
Each fires once; a flag file in `data/work/ops/incident_*.done` records that it happened (delete it to
repeat it).

| Switch | What happens | What the pipeline does |
|---|---|---|
| `download_timeout: "ecmwf:<YYYY-MM-DDTHH>"` | The first download attempt of that forecast run raises a time-out | Prefect retries; the second attempt succeeds and is logged as `recovered` |
| `corrupt_file: "s3:<YYYY-MM-DD>"` | The Sentinel-3 archive of that day arrives truncated | The archive integrity check (`archive_ok`, a zip CRC test) fails before use; the file is moved to `data/raw/quarantine/`, the failed check is stored, the file is downloaded again and the run is logged as `recovered` |
| `ais_outage: {start, hours}` | The AIS service is unavailable for that stretch of the replay clock | AIS ingest for passes in the gap is logged as `failed`; the freshness check raises a stale-data alert; when AIS is back, the missed passes are fetched and matched (`recovered`, "gap filled") and the alert clears |

`MSP_SIMULATE_FAILURE="ecmwf:<YYYY-MM-DDTHH>"` in the environment overrides the time-out switch.

## Runbook

### SNAP fails

`s1_process` alerts and runs the Python fallback for the same pass, so the pass is still processed.
The engine is recorded in `passes.engine`.

1. Read `data/work/logs/snap_<pass_id>.log`.
2. Out of memory: raise the Java heap (`-Xmx` in `/opt/snap/bin/gpt.vmoptions`, 6 GB in the image).
   The `memory_gb` argument of `gpt_command` sets the tile cache, and the heap in `docker` mode.
3. Orbit or DEM download errors: check network access from the container. Downloaded auxiliary data is
   kept under SNAP's auxdata folder.
4. To redo the pass with SNAP, delete `data/work/candidates/<pass_id>.parquet` (otherwise detection
   reuses the old candidates), then run
   `PREFECT_TASKS_REFRESH_CACHE=true msp process-pass <pass_id> --engine snap`.

### A download fails

Prefect retries with the delays listed in `docs/pipeline.md`. The run log shows `retried` rows and
then `recovered` or `failed`.

- Sentinel-1 or Sentinel-3: check `CDSE_USERNAME` and `CDSE_PASSWORD`. A checksum mismatch deletes the
  partial file, so the next attempt starts clean.
- ECMWF: `_get` falls back from the Google Cloud mirror to the AWS mirror and backs off on throttling.
- AIS: `_post` retries 3 times inside each task attempt.

After fixing the cause, replay the affected day (below). Finished tasks are cached and do not run again.

### Incident: download time-out

Alert: none, unless every retry fails. The run log shows a `retried` row, then `recovered`.

- What the pipeline did: retried with back-off; the next attempt fetched the run.
- What a person checks: if time-outs repeat, the provider's status page and the mirror in use
  (`_get` in `src/msp/sources/ecmwf.py` falls back from the Google Cloud to the AWS mirror). Nothing to
  redo when the retry succeeded.

### Incident: damaged file

Alert: a failed `archive_integrity` row in `quality_checks`; the run log shows `recovered` with
"damaged file ... quarantined and downloaded again".

- What the pipeline did: never read the damaged archive; quarantined it and downloaded a clean copy.
- What a person checks: the quarantined file in `data/raw/quarantine/` (size against the catalogue
  size), and whether damaged files repeat for one provider (then check disk and network). Delete the
  quarantined copy once checked.

### Incident: AIS outage

Alert: `ALERT ais: stale` on the replay clock, and `failed` AIS ingest rows for the passes in the gap.

- What the pipeline did: kept processing radar passes; remembered the passes without AIS; once AIS
  answered again, fetched their positions, matched them ("gap filled") and the alert cleared.
- What a person checks: that every pass in the gap has `recovered` AIS rows and matched ships
  (`SELECT pass_id, count(*) FROM ais_positions GROUP BY pass_id`); if the service stays down, replay
  the affected days with `--sources s1,ops` once it is back.

### A source is stale

1. Find the stale source and time in the `freshness` table or on the operations page.
2. Look for `failed` rows of that source's flow in `run_log` around that replay time.
3. No failures means no acquisition: Sentinel-2 days may be missing and Sentinel-1 passes follow the
   orbit cycle. Record it and move on.
4. With failures, fix the cause and replay the range.

### Re-run one pass

```
msp process-pass <pass_id> --engine snap
```

The pass ID has the form `<platform>_<YYYYMMDDTHHMMSS>_R<orbit>` and must be listed in
`config/s1_passes.txt` or `config/s1_passes_winter.txt`. `process-pass` runs `s1_ingest`, `ship_detect` and `ais_and_match` without a
replay time. The raw SAFE is usually deleted after processing, so expect a new download.

### Replay a date range

```
msp backfill --start YYYY-MM-DD --end YYYY-MM-DD
msp backfill --start YYYY-MM-DD --end YYYY-MM-DD --sources s1,ops --engine python
```

Dates must lie inside a study period: summer (5 August to 30 September 2026) or winter (1 December
2025 to 15 March 2026; replay it with `--sources s1,ops`, as winter has no optical, temperature or
forecast inputs). Cached tasks return at once, so a replay only redoes
work that failed or whose inputs or code changed. To force a task to run again, set Prefect's
`PREFECT_TASKS_REFRESH_CACHE=true` for that run. To repeat an incident, delete its flag file in
`data/work/ops/` and replay that day.
