"""Line-shaped detections: long, thin bright features (bridges, cables, piers) rather than ships.

Rule, fixed on the summer development passes before any evaluation (`msp line-rule`):
a detection is line-shaped when it is longer than `max_ship_length_m`, or when its length to width ratio
is above the 99th percentile of that ratio among development detections matched to an AIS ship
(rounded up to the next 0.5). Line-shaped detections stay in the data and carry the flag.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

MAX_SHIP_LENGTH_M = 400.0
PERCENTILE = 99.0


def elongation(length_m, width_m) -> np.ndarray:
    length = np.asarray(length_m, dtype=float)
    width = np.maximum(np.asarray(width_m, dtype=float), 1.0)
    return length / width


def fit_ratio(dev: pd.DataFrame) -> float:
    """dev: development detections with length_m, width_m and a boolean `matched` column."""
    ships = dev[dev["matched"]]
    r = elongation(ships["length_m"], ships["width_m"])
    return math.ceil(float(np.percentile(r, PERCENTILE)) * 2) / 2


def is_line_shaped(length_m, width_m, ratio: float, max_len: float = MAX_SHIP_LENGTH_M) -> np.ndarray:
    length = np.asarray(length_m, dtype=float)
    return (length > max_len) | (elongation(length_m, width_m) > ratio)
