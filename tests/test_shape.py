"""Line-shaped rule: long or very thin detections are flagged; ship-like ones are not."""

import pandas as pd

from msp.detect.shape import elongation, fit_ratio, is_line_shaped


def test_ratio_comes_from_matched_development_detections():
    dev = pd.DataFrame({"length_m": [100, 120, 80, 600, 90], "width_m": [20, 20, 20, 10, 15],
                        "matched": [True, True, True, False, True]})
    r = fit_ratio(dev)
    assert r >= float(elongation([120], [20])[0])  # the most elongated matched ship stays below the rule
    assert r % 0.5 == 0


def test_bridge_like_and_very_long_detections_are_flagged():
    flags = is_line_shaped([180, 450, 60], [10, 60, 20], ratio=8.0)
    assert list(flags) == [True, True, False]
