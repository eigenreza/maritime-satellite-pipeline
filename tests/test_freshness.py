"""Freshness uses only what had arrived by the replay time (ingest slot, AIS outage and gap fill)."""

from datetime import UTC, datetime

import pandas as pd

from msp.flows import arrival, ingest_slot


def _t(*a):
    return datetime(*a, tzinfo=UTC)


SC = {"pass_id": "S1C_20260910T060330_R008", "start": "2026-09-10T06:03:30Z", "published": "2026-09-10T07:10:00Z"}
RL = pd.DataFrame({"replay_time": [pd.Timestamp(_t(2026, 9, 10, 12)), pd.Timestamp(_t(2026, 9, 11, 6))],
                   "status": ["failed", "recovered"],
                   "message": ["AIS service unavailable (outage)", "gap filled after the outage: AIS for S1C_20260910T060330_R008"]})


def test_pass_arrives_at_its_ingest_slot_not_at_acquisition():
    assert ingest_slot(SC) == _t(2026, 9, 10, 12)
    assert arrival([SC], {SC["pass_id"]}, RL, _t(2026, 9, 10, 6)) == {}
    assert SC["pass_id"] in arrival([SC], {SC["pass_id"]}, RL, _t(2026, 9, 10, 12))


def test_ais_missing_during_the_outage_and_back_after_the_gap_fill():
    pid = SC["pass_id"]
    assert arrival([SC], {pid}, RL, _t(2026, 9, 10, 12))[pid][1] is None
    assert arrival([SC], {pid}, RL, _t(2026, 9, 11, 0))[pid][1] is None
    assert arrival([SC], {pid}, RL, _t(2026, 9, 11, 6))[pid][1] == _t(2026, 9, 11, 6)


def test_ais_arrives_with_the_pass_when_nothing_failed():
    other = SC | {"pass_id": "S1D_20260911T055528_R110", "start": "2026-09-11T05:55:28Z", "published": "2026-09-11T07:00:00Z"}
    got = arrival([other], {other["pass_id"]}, RL, _t(2026, 9, 11, 12))
    assert got[other["pass_id"]][1] == _t(2026, 9, 11, 12)


def test_unprocessed_pass_never_counts():
    assert arrival([SC], set(), RL, _t(2026, 9, 12)) == {}
