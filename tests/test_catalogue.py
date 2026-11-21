"""STAC: items built by the pipeline validate against the stored official schemas (offline)."""

from datetime import UTC, datetime

import numpy as np
import pytest
import rasterio

from msp import paths
from msp.catalog.stac import s1_item
from msp.catalog.validate import validate_dict, validate_file
from msp.sources.sentinel2 import write_cog

META = {"pass_id": "S1D_20260806T055526_R110", "platform": "S1D", "relative_orbit": 110,
        "start_time": datetime(2026, 8, 6, 5, 55, 26, tzinfo=UTC),
        "stop_time": datetime(2026, 8, 6, 5, 55, 51, tzinfo=UTC), "orbit_direction": "DESCENDING"}


def _item(fix, tmp_path):
    with rasterio.open(fix / "sigma0_small.tif") as src:
        db = (10 * np.log10(src.read()) * 100).astype("int16")
        tr = src.transform
    out = tmp_path / "s1.tif"
    write_cog(out, db, tr, "EPSG:32632", -32768, {"ACQUISITION_TIME": "2026-08-06T05:55:26Z"})
    it = s1_item(META, out, "snap", tmp_path)
    it.collection_id = None
    return it.to_dict()


def test_s1_item_is_valid_stac_with_extensions(fix, tmp_path):
    d = _item(fix, tmp_path)
    exts = " ".join(d["stac_extensions"])
    for e in ("projection", "raster", "sar", "sat", "processing"):
        assert e in exts
    assert validate_dict(d) == []


def test_invalid_item_is_rejected(fix, tmp_path):
    d = _item(fix, tmp_path)
    d["properties"]["sar:polarizations"] = ["XX"]
    assert validate_dict(d)


@pytest.mark.skipif(not (paths.CATALOG / "catalog.json").exists(), reason="catalogue not built")
def test_committed_catalogue_is_valid():
    files = sorted(paths.CATALOG.rglob("*.json"))
    assert files
    bad = {f.name: validate_file(f) for f in files}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, list(bad.items())[:3]
