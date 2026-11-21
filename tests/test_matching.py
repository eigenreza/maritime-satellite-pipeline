"""AIS interpolation, detection-to-AIS matching, labels and pass-based splits (written before the code)."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from msp.match.rules import assign_splits, greedy_match, interpolate_track, label_detections

T0 = datetime(2026, 8, 6, 6, 0, 0, tzinfo=UTC)


def track(points):
    return pd.DataFrame([{"t": T0 + timedelta(seconds=s), "x": x, "y": y, "sog": 10.0, "cog": 90.0}
                         for s, x, y in points])


def test_linear_interpolation_between_bracketing_points():
    tr = track([(-60, 0.0, 0.0), (60, 120.0, 60.0)])
    r = interpolate_track(tr, T0, max_gap_s=600)
    assert r["x"] == pytest.approx(60.0)
    assert r["y"] == pytest.approx(30.0)
    assert r["gap_s"] == pytest.approx(120.0)


def test_no_position_when_gap_too_large():
    tr = track([(-900, 0.0, 0.0), (900, 100.0, 0.0)])
    assert interpolate_track(tr, T0, max_gap_s=600) is None


def test_dead_reckoning_from_a_close_single_side_point():
    # 10 knots east for 60 s = 308.7 m
    tr = track([(-60, 0.0, 0.0), (-120, -300.0, 0.0)])
    r = interpolate_track(tr, T0, max_gap_s=600, max_extrapolate_s=120)
    assert r["x"] == pytest.approx(10 * 0.514444 * 60, rel=1e-3)
    assert r["y"] == pytest.approx(0.0, abs=1e-6)


def test_no_extrapolation_beyond_limit():
    tr = track([(-400, 0.0, 0.0)])
    assert interpolate_track(tr, T0, max_gap_s=600, max_extrapolate_s=120) is None


def test_greedy_match_is_one_to_one_and_nearest_first():
    pairs = pd.DataFrame({"det_id": [1, 1, 2, 2], "vessel_hash": ["a", "b", "a", "b"],
                          "distance_m": [50.0, 300.0, 60.0, 500.0]})
    m = greedy_match(pairs, max_distance_m=400)
    assert dict(zip(m.det_id, m.vessel_hash, strict=True)) == {1: "a"}
    # det 2 cannot take "a" (taken) and "b" is beyond the limit
    assert 2 not in set(m.det_id)


def test_greedy_match_resolves_competition():
    pairs = pd.DataFrame({"det_id": [1, 1, 2], "vessel_hash": ["a", "b", "a"],
                          "distance_m": [100.0, 150.0, 20.0]})
    m = greedy_match(pairs, max_distance_m=400).sort_values("det_id")
    assert list(m.vessel_hash) == ["b", "a"]


def test_labels_ship_not_ship_and_ambiguous():
    det = pd.DataFrame({"det_id": [1, 2, 3], "nearest_ais_m": [50.0, 5000.0, 800.0]})
    matched = {1}
    lab = label_detections(det, matched, exclusion_distance_m=1500)
    assert list(lab) == ["ship", "not_ship", "ambiguous"]
    det2 = pd.DataFrame({"det_id": [4], "nearest_ais_m": [np.nan]})
    assert list(label_detections(det2, set(), 1500)) == ["not_ship"]


def test_pass_splits_are_by_time_and_never_mix():
    times = pd.date_range("2026-08-05", periods=24, freq="2D", tz="UTC")
    passes = pd.DataFrame({"pass_id": [f"P{i:02d}" for i in range(24)], "start_time": times[::-1]})
    s = assign_splits(passes, dev_fraction=1 / 3, ml_train_fraction=0.5, ml_test_fraction=1 / 3)
    s = s.sort_values("start_time")
    assert (s.research_split.iloc[:8] == "dev").all()
    assert (s.research_split.iloc[8:] == "eval").all()
    assert (s.ml_split.iloc[:12] == "train").all()
    assert (s.ml_split.iloc[12:16] == "calib").all()
    assert (s.ml_split.iloc[16:] == "test").all()
    # Every test pass is later than every training or calibration pass
    assert s[s.ml_split == "test"].start_time.min() > s[s.ml_split != "test"].start_time.max()
