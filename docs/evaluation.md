# Evaluation rules

These rules were written down, and the matching and split settings in `config/pipeline.yaml` were
fixed, before any detection was matched to AIS and before any evaluation number existed. The
detector settings are then tuned on the development passes only, and frozen before the evaluation
passes are scored.

## Passes and splits

- Passes are ordered by acquisition time and split by pass, never by detection.
- Research question (detection reliability): the first third of the passes is the **development**
  set (used to fix the detector settings); the remaining two thirds are the **evaluation** set.
- Ship classifier: the first half of the passes is used for **training**, the next sixth for
  **calibration** and model choice, the last third for **testing**. No cut-out from a test pass is
  seen during training or calibration.

## Matching (frozen)

- Each AIS ship is placed at the radar acquisition time by linear interpolation between its AIS
  points (each at most 600 s away), or by dead reckoning from a point at most 120 s away.
- A detection matches the nearest unmatched AIS ship within **400 m** (greedy, one to one).
- An AIS ship counts as "visible to the radar" when its interpolated position lies inside the imaged
  sea area of the pass (outside the buffered land mask).
- Classifier labels: "ship" = matched; "not a ship" = no AIS ship within **1500 m**; anything in
  between is "ambiguous" and left out of training and scoring.

## Measures

- Detection probability for AIS ships = matched AIS ships / AIS ships visible to the radar, by
  length class (under 30, 30 to 60, 60 to 100, 100 to 200, over 200 m), by wind class (0 to 5, 5 to
  10, 10 to 15, over 15 m/s) and by wave class (0 to 1, 1 to 2, 2 to 3, over 3 m).
- Share of detections without an AIS match, by sea-state class.
- 95% intervals from a bootstrap over passes: 2,000 resamples of the evaluation passes with
  replacement, fixed seed, percentile intervals.
- A logistic regression of detection (yes/no) on log ship length, wind speed and significant wave
  height, with 95% intervals from the same pass bootstrap.
- Cloud blindness: share of Sentinel-2 acquisition days with more than 50% cloud over the observed
  sea, with a bootstrap interval over days.
- Classifier (test passes only): precision, recall, ROC AUC and precision-recall AUC against the
  plain detector; false alarms removed and real ships kept at the operating point chosen on the
  calibration passes (ship probability 0.5 after calibration), overall and per sea-state class;
  calibration curve and expected calibration error (10 bins); length error (median absolute error
  in meters and percent) by length class. Intervals from a bootstrap over test passes.

## Winter: a second held-out test set

The study has two seasons: a summer period (the development, training, calibration, evaluation and
test passes above) and a winter period from 1 December 2025 to 15 March 2026. The winter rules
below were written down before any winter radar pass was processed or any winter number existed.

- **Selection by sea state, before download.** Candidates are every Sentinel-1 IW GRD scene in the
  winter period that covers at least 70% of the study box. For each candidate, the median and the
  maximum wave height and wind over open sea (wave-model cells at least 3 km from land) at the pass
  time decide its stratum: stormy (median waves at least 4 m or median wind at least 15 m/s),
  moderate (median waves 2 to 4 m) or calm. The 6 stormiest, 6 moderate passes spread evenly over
  wave height and the rest calm passes spread evenly over time make 18 passes. A pass with no AIS
  within 10 minutes or no wave-model hour within 90 minutes is dropped. The rule is in
  `src/msp/pipeline/winter.py`; the list with each pass's sea state is `config/s1_passes_winter.txt`.
- **Frozen.** Winter passes are never used for tuning, training, calibration or any threshold. The
  detector settings, matching rules, land and structure masks, the line-shaped rule, the AI weights,
  its temperature and its operating point are the summer ones, unchanged.
- **Same measures, per season.** Every detection rate and every share above is reported for the
  summer evaluation passes, the winter passes, and both together. For the combined set the bootstrap
  resamples passes within each season, so the mix of summer and winter passes stays fixed.
- **Season term.** The logistic regression is fitted on summer evaluation and winter passes together,
  once without and once with a season indicator (winter = 1). With the indicator, the effects of
  length, wind and waves are compared within a season, so a difference between the seasons that is
  not wind or waves (light, traffic mix, fishing season) does not leak into the wave and wind terms.
- **AI on winter.** The summer AI scores the winter passes as it is (no retraining); the same
  measures as on the summer test passes, side by side.

## Missing sea state

Wind and waves at a ship or a detection come from the nearest wave-model sea cell within 2 km and
the nearest model hour within 90 minutes (`metocean.max_cell_distance_m`). Further away, the values
are missing, not zero: such cases are left out of the wind and wave classes and the regression, and
counted in `results/research_summary.json` ("no wave data at this spot"). The model has no exact
zeros at sea. Very small values (a few centimeters) are real: they come from sheltered water in
fjords and sounds, and the demo shows them as "under 0.1 m (sheltered water)".

## The ship tool range

The demo's "Would radar see this ship?" tool answers only inside the range the data covers: wind and
waves up to the 97.5th percentile met by AIS ships (rounded down) and lengths inside their central
95% (`observed_range` in `src/msp/evaluate/research.py`, tested). Each answer carries a 95% interval
from the pass bootstrap draws of the regression.
