# Technical decisions

Each row names a decision and the main reason for it. Configuration values are in
`config/pipeline.yaml`. Evaluation rules are in `docs/evaluation.md`.

Terms: SAR, synthetic aperture radar (a satellite radar that sees through clouds and at night). AIS,
automatic identification system (ship position reports). COG, cloud-optimized GeoTIFF (an image file
that can be read in pieces over the web). STAC, a standard catalog format for satellite data. CFAR,
constant false alarm rate (compares each pixel with its local sea background). SNAP, ESA's toolbox for
Sentinel data. GDAL, the standard library for raster formats. GRIB2, the binary format of weather
model output. MMSI, a ship's radio identity number. CNN, convolutional neural network (a model that
learns patterns from images). Prefect, a Python workflow engine. PostGIS, the spatial extension of
PostgreSQL.

## Area and period

| Decision | Reason |
|---|---|
| Study box 4.0 to 6.0 E, 60.0 to 61.3 N | Covers the Fedje and Bergen approaches, busy shipping lanes with open sea and fjords in one box |
| Working projection UTM zone 32N (EPSG:32632), 10 m pixels | Distances and areas in meters, matching the Sentinel-1 GRD pixel spacing |
| Summer period 5 August to 30 September 2026 | Long enough for many passes, small enough for the download budget; it holds the development, training, calibration, evaluation and test passes |
| Winter period 1 December 2025 to 15 March 2026, 18 passes chosen by sea state before download | Summer had few rough seas; winter brings the storms needed to answer the wind and wave question. Choosing by sea state, not by date, guarantees stormy, moderate and calm passes |
| Winter as a second held-out test set, with the summer settings and the summer AI frozen | Tests whether rules and model tuned in one season hold in another, with nothing adjusted after seeing winter |
| Bootstrap within seasons for combined results | The mix of summer and winter passes stays fixed in every resample |
| Logistic regression with and without a season term | With the term, wind and wave effects are compared within a season, so other seasonal differences do not leak into them |
| No data after 30 September 2026 anywhere | One fixed end makes every result reproducible |

## Sources

| Decision | Reason |
|---|---|
| 24 summer Sentinel-1 passes on relative orbits 110, 8 and 117 | Chosen by area coverage within a raw download budget, listed in `config/s1_passes.txt` |
| 18 winter passes on relative orbits 8, 15 and 110 | Every scene covering at least 70% of the box was a candidate; the selection rule picks by sea state over open sea (`src/msp/pipeline/winter.py`, list in `config/s1_passes_winter.txt`) |
| Sentinel-1 from Copernicus Data Space | The official source of full SAFE products, with MD5 checksums for every download |
| Sentinel-2 L2A from Planetary Computer, windowed COG reads at 60 m | Cloud statistics need only the scene classification layer, so no full tile is downloaded |
| Sentinel-3 SST from Copernicus Data Space | The Planetary Computer collection ends before the study period |
| One Sentinel-3 product every second day, non-time-critical first | Enough for the sea temperature context at a fraction of the volume |
| MET Norway MyWaveWAM 800 m for waves, from the daily analysis files | Fine grid that resolves the coast and fjords, archived for both seasons and readable in slices over OPeNDAP (a remote data access protocol) |
| Sea state from the nearest wave-model sea cell within 2 km, otherwise missing | A value from a cell far away would be wrong; missing stays missing and is counted, never written as zero |
| The wave model's forcing wind for wind | Wind and waves come from one consistent model run and one grid |
| ECMWF open data from the Google Cloud mirror | The AWS mirror throttled the many small range requests. It stays as the fallback. |
| ECMWF runs 00z and 12z, steps 0 and 6 h | Two runs a day give a forecast for every 6-hour slot of the replay |
| Only the needed GRIB2 messages, through byte ranges from the `.index` files | Downloads a few fields instead of the full global files |
| Kystverket open AIS endpoints | No login needed, with positions and static data for Norwegian waters |
| Kartverket N50 land-cover polygons for the land mask | Detailed national coastline, including skerries and small islands; open sea beyond the edge of the county data stays sea |
| Aquaculture sites and Sodir facilities as structures | Fish farms and platforms are fixed bright targets that look like ships |
| Bridges, piers, breakwaters, groynes and power lines over water from OpenStreetMap | Long fixed structures look like ships to the detector; OpenStreetMap maps them openly (ODbL) |

## Radar processing

| Decision | Reason |
|---|---|
| ESA SNAP 14 built from the official installer | Older SNAP images cannot calibrate Sentinel-1C and Sentinel-1D |
| BEAM-DIMAP output (SNAP's native format) plus GDAL stacking, not SNAP's GeoTIFF writer | Much faster for the same pixels |
| Stage each scene to container storage before SNAP runs | Random reads from a Windows host folder mounted in Docker are very slow |
| Pure-Python fallback with lookup-table calibration and ground-control-point geocoding | A pass is still processed when SNAP fails, and the two engines can be compared |
| Precise orbit with `continueOnFail` | A missing orbit file does not stop a pass |
| Product at 20 m, dB as int16 with scale 0.01 | Half the size of float32 at 0.01 dB resolution, enough for display and statistics |
| Delete the 10 m working raster and the SAFE after detection | Keeps disk use bounded during a full replay |

## Detection and matching

| Decision | Reason |
|---|---|
| Cell-averaging CFAR on VV in linear sigma0 | The threshold follows the local sea, so rough water does not flood the output |
| Two-stage detector with stored candidates and cut-outs | Thresholds can be tuned and reapplied without new SAR processing |
| Second-stage thresholds tuned on development passes only | Evaluation passes stay unseen until the settings are frozen |
| Land mask buffered 250 m, structures 150 m | Quays, rocks and fish farms are bright and must not count as ships |
| Line-shaped flag fixed on development passes, detections kept | Long thin echoes are likely structures, but removing them would change the rates after the fact; flagged, they stay visible and explainable |
| Acquisition time per latitude | A scene takes about 25 seconds to record, and ships move during that time |
| Greedy one-to-one matching within 400 m | Deterministic, and close to optimal when ships are far apart compared with the match distance |
| Matching in PostGIS with `ST_DWithin` and `<->` | Spatial indexes do the neighbor search, and the SQL is easy to inspect |
| Salted hashes instead of MMSI | No public file can be linked back to a ship |

## Classifier and evaluation

| Decision | Reason |
|---|---|
| Labels from AIS matching, with a 1500 m exclusion zone | Free labels at scale, and unclear cases are left out instead of guessed |
| Pass-based splits by time | Detections of one pass share sea state and ships, so a random split would leak |
| Gradient boosting baseline next to the CNN | Shows how much the image itself adds over the detector features |
| Temperature scaling on calibration passes | Probabilities that match observed frequencies, with one parameter |
| Bootstrap over passes for all intervals | Passes are the independent units, not detections |

## Operations

| Decision | Reason |
|---|---|
| Prefect 3 with retries, input caching and tag concurrency limits | Reruns are cheap, failures recover, and SNAP never runs twice at once |
| PostGIS in Docker on host port 15432 | Avoids clashes with a local PostgreSQL and with port ranges Windows reserves |
| Replay clock instead of wall-clock timestamps in public outputs | Run history, freshness and alerts read as if the pipeline ran live in the period, and outputs do not change with the day they were made |
| PNG figures saved without software and time metadata | Figures carry no build time or software stamp |
| Demo as static files behind a small FastAPI server | No database or keys in the hosted demo |
| Scenes fetched in the first 6-hour slot after the provider published them | The replay follows when data really became available, so delivery times are honest |
| Three incidents switched on in the configuration | Time-out, damaged file and source outage show retry, quarantine and alert-then-recover paths without editing data |
| A light half-resolution radar overlay drawn first on the map | The first pass appears quickly; the full image replaces it once loaded |
