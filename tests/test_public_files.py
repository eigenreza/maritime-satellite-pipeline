"""The public tree must be clean: hygiene and privacy scan, README tables, results without work dates."""

import json

import pytest

from msp import paths
from msp.report import readme, scans


def test_public_files_pass_hygiene_and_privacy_scan():
    problems = scans.run()
    assert not problems, "\n".join(problems[:20])


@pytest.mark.skipif(not (paths.RESULTS / "research_summary.json").exists(), reason="no results yet")
def test_readme_tables_match_results():
    assert readme.check()


@pytest.mark.skipif(not paths.RESULTS.exists(), reason="no results yet")
def test_results_are_valid_json():
    for f in paths.RESULTS.glob("*.json"):
        json.loads(f.read_text())
