# Ship detection and AIS matching

Ships are bright points on a dark sea in SAR images (synthetic aperture radar: a satellite radar that
sees through clouds and at night). The detector finds bright objects at sea. Matching then checks each
one against AIS, the automatic identification system in which ships broadcast their position.

## CFAR detector

CFAR, constant false alarm rate: compares each pixel with its local sea background, so the threshold
rises when the sea is rough and bright. `cfar_mask` in `src/msp/detect/cfar.py` implements
cell-averaging CFAR on the VV band in linear sigma0 (calibrated radar backscatter):

- The guard window (`2 * guard_px + 1` pixels, 150 m) keeps the ship out of its own background. The
  ring between it and the background window (`2 * background_px + 1` pixels, 510 m) gives the sea
  statistics.
- A pixel is a candidate when `sigma0 > mean + k * std` of the ring, with `k = pfa_sigma`. Only sea
  pixels count, and at least a quarter of the ring must be valid sea.
- Window sums come from box filters, and large scenes run in overlapping 2048-pixel tiles.

Settings (`detector` in `config/pipeline.yaml`): `guard_px` 7, `background_px` 25, `pfa_sigma` 5.0,
`min_pixels` 4, `max_length_m` 450, `merge_distance_px` 3.

## From pixels to objects

`detect` groups candidate pixels into objects:

1. **Clustering.** The mask is dilated `merge_distance_px` times, so bright pixels separated by small
   gaps join one object. The dilated mask is labeled with 8-connectivity, and labels are kept only on
   the original bright pixels.
2. **Size.** Objects with fewer than `min_pixels` pixels are dropped. The position is the
   brightness-weighted centroid.
3. **Length, width and heading.** The principal axes of the pixel cloud (eigenvectors of its
   covariance) give the orientation. Length and width are the extents along the major and minor axis
   plus one pixel, times the pixel size. Heading is the major axis from north, 0 to 180 degrees.
   Objects longer than `max_length_m` are dropped.
4. **Fill ratio.** `fill = n_pixels / (length_px * width_px)`. Objects below `min_fill` (0.3, a
   default in `DetectorParams`) are scattered patches, not compact targets, and are dropped.
5. **Brightness.** Peak and mean VV and peak VH in dB, the sea background as the median of a ring of
   `background_px` pixels around the object, and the signal-to-clutter ratio `scr_db = peak - background`.

## Two-stage design

**Stage 1** (`detect_pass` in `src/msp/pipeline/s1.py`) runs CFAR once per pass on the 10 m working
raster. It stores every candidate in `data/work/candidates/<pass_id>.parquet` and a 64 x 64 pixel VV
and VH cut-out in dB around each one in `data/work/chips/<pass_id>.npz`.

**Stage 2** (`finalize`) keeps candidates with `scr_db >= min_scr_db` and `peak_vv_db >= min_peak_db`
and writes the detection product `data/products/detections/<pass_id>.parquet`. `det_idx` stays the
candidate index, so a detection always links to its cut-out.

`msp tune-detector` (`src/msp/detect/tune.py`) fixes the two thresholds on the development passes
only. It tries every pair from a grid of contrast values (6 to 30 dB) and peak values (no limit, or
-18 to +10 dB), matches the filtered candidates to AIS with the frozen rules and ranks the pairs by
F1 score (the balance of AIS ships found and detections explained by AIS). The grid goes to
`results/detector_tuning_dev.csv` and the best pair into the `detector` section of the config, where
it stays frozen. `msp redetect` applies it to all passes from the stored candidates.

The F1 surface is flat near its top: pairs that find many more ships, with many more false alarms,
score almost the same as stricter pairs that find fewer. The rule takes the best score as it is, so
the detection rates in the results belong to that operating point. The ship classifier is the second
step that removes most of the extra false alarms.

## Land and structure masks

`build_land_mask` in `src/msp/sources/landmask.py` reads Kartverket N50 map data: every land-cover
polygon of the `N50_Arealdekke_omrade` layer except the sea surface (`objtype = 'Havflate'`). Land is
their union inside the study box, written as `land.shp`; gaps under 0.05 km2 between neighboring
polygons are filled. Building land from land polygons, not as "box minus sea", matters here: the N50
county data ends some way offshore, and everything beyond its edge is open sea. A copy buffered by
`landmask.buffer_m` (250 m) is written as `land_buffered.shp`, so piers, quays and rocks at the shore
do not count as ships.

After a change to the mask, `msp reprocess` runs the first detector stage again for every pass. The
10 m working rasters are deleted after processing, so it downloads each scene again, runs SNAP, finds
the candidates and cleans up; `msp redetect` then rebuilds the final detections and the matching.

`fetch_structures` collects fixed bright targets as GeoParquet: aquaculture sites at sea from
Fiskeridirektoratet and petroleum facilities from Sodir (the Norwegian Offshore Directorate) as
points, and fixed linear structures over water from OpenStreetMap (ODbL) as lines: bridges
(`bridge=*` ways and `man_made=bridge`), piers, breakwaters, groynes and power lines
(`fetch_linear_structures`, one Overpass query for the study box). Only lines with a part over water
are kept; lines entirely on land are already inside the land mask. Each record carries its kind.
`sea_mask_for_grid` rasterizes the buffered land and the structures, each buffered by 150 m (points
as discs, lines as corridors), onto the image grid, and keeps only pixels inside the study box.
Detection runs only where this mask is true.

## Line-shaped detections

Some bright targets are long and thin: a bridge or cable the mask does not cover, a pier, a ship's
wake. `src/msp/detect/shape.py` flags a detection as **line-shaped** when it is longer than 400 m, or
when its length to width ratio is above a fixed limit. The limit is the 99th percentile of that ratio
among development-pass detections matched to an AIS ship, rounded up to the next 0.5; `msp line-rule`
computes it on the summer development passes only and writes it to `detector.line_max_elongation`
before any evaluation. Line-shaped detections stay in the data and in every rate; the map and the
game show them with that label, and the game's note says that a long straight line is more likely a
structure than a boat.

## AIS ingest

`ingest_ais_for_pass` in `src/msp/pipeline/ais_match.py`:

1. Fetches all positions in the study box from the open Kystverket endpoints, `ais.window_minutes`
   (20) before and after the middle of the pass, and keeps the raw file private in `data/raw/ais/`.
2. Replaces each MMSI (the ship's radio identity) with a salted hash: `vessel_hash` takes SHA-256 of
   `salt:mmsi` and keeps `v` plus 11 hex characters. The salt comes from `MSP_HASH_SALT` or from
   `data/work/secrets/vessel_salt.txt`, created at random on first use, and never leaves the machine.
3. Fetches static data per month and keeps only a type class, the length and a length class per hash
   in `vessels`.

**Acquisition time per latitude.** A Sentinel-1 scene is recorded over about 25 seconds along the
orbit. `acquisition_time` gives each ship the time at its own latitude, linear between the scene's
start and stop over the footprint's latitude range, reversed for descending passes (north to south).

**Interpolation and dead reckoning.** `interpolate_track` in `src/msp/match/rules.py` uses two AIS
points around that time, each at most `matching.max_time_offset_s` (600 s) away, for linear
interpolation. Otherwise it takes the closest point, if at most 120 s away, and moves it with its speed
and course over ground (dead reckoning). Otherwise the ship has no position for this pass.

Results go to `ais_at_pass`. `in_scene` is true when the position lies inside the pass's imaged sea
area (`passes.valid_area`), so the radar could have seen the ship.

## Matching

`match_pass` collects all detection and ship pairs within `matching.max_distance_m` (400 m) in
PostGIS. `greedy_match` takes the closest remaining pair, removes both partners and repeats, with ties
broken by `det_id` and `vessel_hash`.

Candidate pairs (`CANDIDATES_SQL`):

```sql
-- Candidate detection-ship pairs: every AIS ship within the match distance of a detection (same pass)
SELECT d.det_id, a.vessel_hash, ST_Distance(d.geom, a.geom) AS distance_m
FROM detections d
JOIN ais_at_pass a ON a.pass_id = d.pass_id AND ST_DWithin(d.geom, a.geom, %(dist)s)
WHERE d.pass_id = %(pass_id)s
```

Nearest AIS ship per detection (`NEAREST_SQL`), used for the classifier labels:

```sql
-- Distance from each detection to the nearest AIS ship of the same pass (k-nearest-neighbour index scan)
SELECT d.det_id, n.distance_m AS nearest_ais_m
FROM detections d
LEFT JOIN LATERAL (
    SELECT ST_Distance(d.geom, a.geom) AS distance_m
    FROM ais_at_pass a WHERE a.pass_id = d.pass_id
    ORDER BY d.geom <-> a.geom LIMIT 1
) n ON true
WHERE d.pass_id = %(pass_id)s
```

Both queries use the spatial (GiST) indexes on `geom`. Geometries are in UTM 32N, so distances are in meters.

## Research queries

`src/msp/evaluate/research.py` reads the evaluation data with two queries:

```sql
-- Every AIS ship the radar could have seen (inside the imaged sea area), with its length, the sea state
-- at its position, and whether a detection was matched to it
SELECT a.pass_id, p.start_time, a.vessel_hash, v.length_m, v.length_class, v.type_class,
       a.wind_speed, a.wave_hs, a.sog,
       (m.det_id IS NOT NULL) AS detected
FROM ais_at_pass a
JOIN passes p USING (pass_id)
LEFT JOIN vessels v USING (vessel_hash)
LEFT JOIN matches m ON m.pass_id = a.pass_id AND m.vessel_hash = a.vessel_hash
WHERE a.in_scene

-- Every radar detection at sea with its AIS match (if any) and the sea state at its position
SELECT d.det_id, d.pass_id, p.start_time, d.length_m, d.peak_vv_db, d.scr_db, d.wind_speed, d.wave_hs,
       d.ship_prob, (m.vessel_hash IS NOT NULL) AS matched
FROM detections d
JOIN passes p USING (pass_id)
LEFT JOIN matches m USING (det_id)
```

Rules are in `docs/evaluation.md`, rates with intervals in `results/` and the README tables.

## Known limits

- **Azimuth shift.** A ship moving toward or away from the radar appears displaced along the flight
  direction in a SAR image. The offset grows with that speed component and can push a fast ship beyond
  the match distance from its true AIS position.
- **Hidden boats.** The open AIS service hides non-public ships, for example fishing vessels under
  15 m and leisure craft under 45 m. Such boats can show up as unmatched detections even when they are
  real.
- **Shore zone.** Nothing within 250 m of land or 150 m of a mapped structure is detected.
- **Unmapped structures.** A bridge, cable or fish farm missing from the open maps can still show up
  as a detection without an AIS match; the line-shaped flag catches the long, thin ones.
