"""Replay slots, calibration metric and temperature scaling, vessel privacy, hygiene and privacy scans."""

from datetime import UTC, datetime

import numpy as np

from msp.flows import slot
from msp.ml.models import expected_calibration_error, fit_temperature
from msp.report.scans import scan_text
from msp.sources.ais import length_class, type_class, vessel_hash


def test_replay_slot_is_after_acquisition():
    assert slot(datetime(2026, 8, 6, 5, 55, tzinfo=UTC)) == datetime(2026, 8, 6, 6, 0, tzinfo=UTC)
    assert slot("2026-08-06T23:10:00Z") == datetime(2026, 8, 7, 0, 0, tzinfo=UTC)


def test_expected_calibration_error_perfect_and_bad():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 20000)
    y = (rng.uniform(0, 1, 20000) < p).astype(int)
    ece, curve = expected_calibration_error(p, y)
    assert ece < 0.02 and len(curve) == 10
    ece_bad, _ = expected_calibration_error(np.clip(p * 0.3 + 0.7, 0, 1), y)
    assert ece_bad > 0.2


def test_temperature_scaling_recovers_overconfidence():
    rng = np.random.default_rng(1)
    true_logit = rng.normal(0, 2, 20000)
    y = (rng.uniform(0, 1, 20000) < 1 / (1 + np.exp(-true_logit))).astype(int)
    t = fit_temperature(true_logit * 3.0, y)  # a model three times too confident
    assert 2.5 < t < 3.5


def test_vessel_hash_is_stable_and_not_the_number():
    mmsi = int("257" + "000000")
    h1, h2 = vessel_hash(mmsi, "salt"), vessel_hash(mmsi, "salt")
    assert h1 == h2 and str(mmsi) not in h1 and vessel_hash(mmsi, "other") != h1


def test_classes():
    assert type_class(70) == "cargo" and type_class(84) == "tanker" and type_class(None) == "unknown"
    assert length_class(25) == "under 30 m" and length_class(250) == "over 200 m" and length_class(0) == "unknown"


def test_privacy_scan_catches_mmsi_and_imo():
    assert any("MMSI" in p for p in scan_text("x.json", '{"id": ' + "257" + "123450}"))
    assert any("IMO" in p for p in scan_text("x.md", "IMO " + "9" + "123456"))
    assert not scan_text("x.json", '{"x": 6811321.06, "bbox": [4.0, 60.0]}')


def test_hygiene_scan_catches_dashes_and_late_dates():
    assert scan_text("x.md", "a " + chr(0x2014) + " b")
    assert scan_text("x.md", "processed 2026-1" + "0-02")
    assert not scan_text("x.md", "the study period ends 2026-09-30")


def test_run_log_finds_replay_time_passed_positionally(monkeypatch):
    from msp.ops import runlog

    seen = []
    monkeypatch.setattr(runlog, "record", lambda flow, task, status, replay, *a, **k: seen.append((status, replay)))
    monkeypatch.setattr(runlog, "_attempt", lambda: 1)

    @runlog.logged("demo")
    def task_fn(x, replay_time=None):
        return {"records": x}

    clock = datetime(2026, 9, 1, 6, tzinfo=UTC)
    task_fn(3, clock)                 # positional, as Prefect may call it
    task_fn(3, replay_time=clock)     # keyword
    assert seen == [("completed", clock), ("completed", clock)]


def test_freshness_alerts_when_stale(monkeypatch):
    from datetime import timedelta

    from msp.ops import runlog

    alerts = []
    monkeypatch.setattr(runlog.db, "execute", lambda *a, **k: None)
    monkeypatch.setattr(runlog, "alert", lambda src, msg, t=None: alerts.append(src))
    clock = datetime(2026, 9, 10, 12, tzinfo=UTC)
    ok = runlog.check_freshness("ecmwf-forecast", clock, clock - timedelta(hours=12))
    old = runlog.check_freshness("ecmwf-forecast", clock, clock - timedelta(hours=40))
    none = runlog.check_freshness("sentinel-2", clock, None)
    assert (ok["status"], old["status"], none["status"]) == ("fresh", "stale", "missing")
    assert alerts == ["ecmwf-forecast", "sentinel-2"]
