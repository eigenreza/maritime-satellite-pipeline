"""Research statistics are reproducible, and demo texts follow each case's own conditions."""

import numpy as np
import pandas as pd

from msp.demo.export import _game_note
from msp.evaluate.research import bootstrap_rate


def _ships(seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = 400
    return pd.DataFrame({
        "pass_id": rng.choice([f"P{i:02d}" for i in range(12)], n),
        "cls": rng.choice(["small", "big"], n),
        "detected": rng.random(n) < 0.5,
    })


def test_bootstrap_does_not_depend_on_row_order():
    df = _ships()
    a = bootstrap_rate(df, "cls", "detected", n=300, seed=7)
    b = bootstrap_rate(df.sample(frac=1.0, random_state=1), "cls", "detected", n=300, seed=7)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def test_game_notes_match_the_sea_state_and_ship_size():
    calm_small = _game_note(True, "hard", 15.0, 2.0, 0.1, 0.4)
    rough_ship = _game_note(True, "hard", 80.0, 12.0, 2.2, 0.4)
    calm_unmatched = _game_note(False, "hard", None, 3.0, 0.2, 0.8)
    assert "small" in calm_small and "rough" not in calm_small
    assert "rough" in rough_ship
    assert "rough" not in calm_unmatched
    compact = _game_note(False, "hard", None, 3.0, 0.2, 0.8, elongation=1.8, fill=0.6)
    stretched = _game_note(False, "hard", None, 3.0, 0.2, 0.8, elongation=9.0, fill=0.2)
    line = _game_note(False, "hard", None, 3.0, 0.2, 0.8, line=True, elongation=25.0, fill=0.1)
    assert "compact" in compact and "calm" in compact
    assert "compact" not in stretched  # never "compact" for a stretched echo
    assert "line" in line and "compact" not in line
    big_rough = _game_note(True, "hard", 209.0, 6.6, 2.0, 1.0, elongation=3.0, fill=0.5)
    assert "hides" not in big_rough and "stands out" in big_rough
    big_stretched = _game_note(True, "easy", 150.0, 3.0, 0.2, 0.99, elongation=7.0, fill=0.3)
    assert "compact" not in big_stretched


def test_ship_tool_range_never_exceeds_the_observed_data():
    import numpy as np
    import pandas as pd

    from msp.evaluate.research import observed_range

    rng = np.random.default_rng(1)
    for _ in range(20):
        n = int(rng.integers(30, 400))
        df = pd.DataFrame({"wave_hs": rng.gamma(2, 0.6, n), "wind_speed": rng.gamma(3, 2.5, n),
                           "length_m": rng.lognormal(3.6, 0.8, n)})
        r = observed_range(df)
        assert r["wave_max_m"] <= np.percentile(df.wave_hs, 97.5) <= df.wave_hs.max()
        assert r["wind_max_ms"] <= np.percentile(df.wind_speed, 97.5) <= df.wind_speed.max()
        assert df.length_m.min() <= r["length_min_m"] <= r["length_max_m"] <= df.length_m.max()
        assert r["ships_wind_over_15"] == int((df.wind_speed >= 15).sum())


def test_exported_tool_range_is_inside_the_results():
    import json

    import pytest

    from msp.config import ROOT

    s, r = ROOT / "web" / "public" / "data" / "summary.json", ROOT / "results" / "research_summary.json"
    if not (s.exists() and r.exists()):
        pytest.skip("no exported demo data")
    exported, result = json.loads(s.read_text()).get("tool_range"), json.loads(r.read_text()).get("tool_range")
    if not (exported and result):
        pytest.skip("results from before the tool range")
    for k in ("wave_max_m", "wind_max_ms", "length_max_m"):
        assert exported[k] <= result[k]
    assert exported["length_min_m"] >= result["length_min_m"]
