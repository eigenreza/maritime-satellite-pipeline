# Ship classifier

Many CFAR detections are not ships: bright wave crests, rain cells and other sea clutter. (CFAR,
constant false alarm rate: compares each pixel with its local sea background.) The classifier looks at
each detection and estimates the probability that it is a ship, plus the ship's length. The code is in
`src/msp/ml/`.

## Labels from matching

Labels come from the AIS matching step, with no manual labeling (AIS, the automatic identification
system, is the radio system in which ships report their position). `label_detections` in
`src/msp/match/rules.py` assigns:

| Label | Rule |
|---|---|
| `ship` | Matched to an AIS ship under the frozen rules (400 m, one to one) |
| `not_ship` | No AIS ship within `matching.exclusion_distance_m` (1500 m) of the detection |
| `ambiguous` | Unmatched, but an AIS ship lies within 1500 m |

Ambiguous detections are left out of training and scoring. They may be a ship whose AIS position is
off, for example through azimuth shift or a gap in its track, and labeling them either way would add
noise.

Unmatched does not always mean false alarm. The open AIS service hides small fishing vessels and
leisure craft, so a real boat without public AIS ends up as `not_ship`. The `not_ship` class is
therefore "no known ship nearby", and the scores measure agreement with AIS, not with the full truth.

The distance to the nearest AIS ship comes from `NEAREST_SQL` in `src/msp/pipeline/ais_match.py`.
`load_table` in `src/msp/ml/data.py` joins detections, matches, ship classes and splits into one table.

## Inputs

**Cut-outs.** Stage 1 of the detector stores a 64 x 64 pixel cut-out at 10 m (640 m square) around
every candidate, with VV and VH sigma0 in dB, NaN outside the image. `load_chips` reads them in table
order. `normalize_chips` maps dB to a roughly unit range with `(dB + 20) / 10`, sets missing pixels to
-1 and clips to -3 to 5.

**Scalar features.**

- Baseline: `FEATURES` in `src/msp/ml/data.py`: pixel count, fill ratio, detector length and width,
  peak and mean VV, peak VH, background VV, signal-to-clutter ratio, wind speed and wave height.
- CNN: `SCALARS` in `src/msp/ml/train.py`: the same eleven values, standardized with the mean and
  standard deviation of the training passes, next to the image. Missing values become 0 after
  standardization. The image branch then has to add what the measurements alone miss.

Wind and wave values come from the MET Norway wave model at each detection (`sea_state_task`).

## Models

**Baseline.** `fit_baseline` in `src/msp/ml/models.py` trains a `HistGradientBoostingClassifier`
(300 iterations, learning rate 0.05, 15 leaves, L2 regularization 1.0) on the scalar features.
`fit_baseline_length` trains a `HistGradientBoostingRegressor` with absolute-error loss on the log
of the AIS length.

**ShipNet.** A CNN, convolutional neural network: a model that learns patterns directly from the image.

- Three convolution blocks (two 3 x 3 convolutions with batch normalization and ReLU, then 2 x 2 max
  pooling) with 16, 32 and 64 channels, then global average pooling.
- The image features are joined with the scalar features and passed through a dense layer of 64 units
  with dropout 0.3.
- Two heads: a classification logit (ship or not) and a regression of log length. The length head
  works in units of the spread of log length over the training ships and is bounded to plus or minus
  three spreads (`tanh`), so an odd cut-out cannot produce an absurd length.

Training (`train_cnn`):

- Loss: binary cross-entropy with a positive class weight (ratio of negatives to positives, clamped
  to 0.2 to 5), plus 0.5 times the absolute error of log length (in training spreads) for ships with
  a known AIS length.
- AdamW optimizer (weight decay 0.001) with a one-cycle learning rate schedule, batches of 64, 60 epochs by default
  (`msp ml train --epochs`).
- Augmentation: each batch is rotated by a random multiple of 90 degrees (`rot90`) and flipped
  horizontally with probability 0.5. Ships can point in any direction, so the model should not
  depend on orientation.
- The weights of the epoch with the lowest log loss on the calibration passes are kept.

## Pass-based splits

`assign_splits` orders passes by acquisition time and splits by pass, never by detection: the first
half is `train`, the next sixth `calib`, the last third `test`. Detections from the same pass share sea
state and often the same ships, so a split by detection would leak information into the test set.
`run` asserts that no test pass appears in training or calibration.

## Calibration

Temperature scaling: the ship probability is `sigmoid(logit / T)`. `fit_temperature` searches T on a
log grid from 0.25 to 8 and keeps the value with the lowest log loss on the calibration passes. After
calibration "90% ship" should mean about 9 in 10. The operating threshold is 0.5.

## Metrics and bootstrap

`run` in `src/msp/ml/train.py` scores three methods on the test passes: the plain detector (every
detection is a ship), the baseline and ShipNet.

- Precision (share of predicted ships that are ships), recall (share of ships found), ROC AUC and
  precision-recall AUC (how well the probability ranks ships above non-ships, 1 is perfect).
- False alarms removed: share of `not_ship` detections below the threshold.
- Real ships kept: share of `ship` detections at or above the threshold.
- Expected calibration error over 10 bins (average gap between predicted probability and observed
  ship share), and the calibration curve.
- The same false-alarm and ship-kept shares per wave-height class (classes with at least 10 detections).
- Length error: median absolute error in meters and in percent, overall and per length class.

Intervals are 95% percentile intervals from a bootstrap over test passes: whole passes are resampled
with replacement `evaluation.bootstrap_resamples` (2000) times. Rules are in `docs/evaluation.md`.

Results:

- `results/ml_scores.json`: split sizes, temperature, threshold and all scores with intervals,
- `results/ml_by_sea_state.csv`: false alarms removed and ships kept per wave-height class,
- `results/ml_length_error.csv`: length errors per model and length class.

The README tables `ml`, `ml_sea` and `length` are written from these files by `msp readme-tables`.

## Winter test, no retraining

The winter passes (1 December 2025 to 15 March 2026, chosen by sea state, see `docs/evaluation.md`)
are a second held-out test set. `msp ml evaluate` (`evaluate_frozen` in `src/msp/ml/train.py`) loads
the committed `models/shipnet.pt` and `models/gradient_boosting.joblib` as they are: same weights,
same temperature, same 0.5 operating point, nothing refitted. It scores the summer test passes and
the winter passes with `evaluate_set`, the same metrics and pass bootstrap as above, and writes them
side by side: `ml_scores.json` holds `scores` (summer test, as before) and `seasons` (summer and
winter, with pass and label counts); the two CSV files gain a `season` column. Winter labels use the
frozen summer rules (ship within 400 m, not a ship beyond 1.5 km, unclear cases left out).

`msp ml train` is never run on winter data. The README tables `ml` (summer test) and `ml_winter`
(winter) and the AI page's Test set switch show both.

## Error gallery

`gallery` saves the most confident mistakes on the summer test and winter passes, up to 6 in each direction and season: false
alarms called ships, and ships called false alarms. Each VV cut-out is written as a PNG without
timestamps to `web/public/data/gallery/`, with `gallery.json` holding the probability, the truth, the
sea state, the detector length and the AIS classes.

## Model files

`run` saves:

- `models/shipnet.pt`: network weights, scalar names, standardization statistics, temperature and threshold,
- `models/gradient_boosting.joblib`: the baseline classifier and length regressor with their feature list,
- `data/work/ml_predictions.parquet`: predictions for every labeled detection.

## The ship_classify flow

`msp ml classify` starts `classify_flow` in `src/msp/flows.py`. The flow uses the first 12 hex
characters of the SHA-256 of `models/shipnet.pt` as the model version and runs `classify_task`.
`classify_all` calls `apply_models`, which runs ShipNet with the stored temperature on every detection,
ambiguous ones included. It then:

- writes `ship_prob` and `est_length_m` to the `detections` table,
- adds the same two columns to each detection GeoParquet in `data/products/detections/`.
