"""End to end on fixture data through Prefect flows: detection, PostGIS matching, STAC item.

Uses a separate PostGIS schema so it never touches pipeline tables. Skipped when no database is reachable.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import rasterio
from prefect import flow, task
from prefect.testing.utilities import prefect_test_harness

from conftest import FIX, db_available

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not db_available(), reason="PostGIS not reachable")]

PASS = "S1D_20260806T055526_R110"
T0 = datetime(2026, 8, 6, 5, 55, 30, tzinfo=UTC)


@pytest.fixture
def isolated_db(monkeypatch):
    from msp import db
    from msp.config import db_url

    base = db_url()
    db.execute("DROP SCHEMA IF EXISTS msp_it CASCADE; CREATE SCHEMA msp_it", url=base)
    sep = "&" if "?" in base else "?"
    monkeypatch.setenv("MSP_DB_URL", f"{base}{sep}options=-csearch_path%3Dmsp_it,public")
    db.init_schema()
    yield
    db.execute("DROP SCHEMA IF EXISTS msp_it CASCADE", url=base)


@task
def detect_fixture() -> list[dict]:
    from msp.detect.cfar import DetectorParams, detect
    from msp.sources.landmask import sea_mask_for_grid

    with rasterio.open(FIX / "sigma0_small.tif") as src:
        vv, vh, tr = src.read(1), src.read(2), src.transform
    sea = sea_mask_for_grid(FIX / "land_small.shp", None, tr, vv.shape[1], vv.shape[0])
    det = detect(vv, vh, sea, tr, DetectorParams(min_pixels=3))
    return det.to_dict("records")


@task
def load_to_postgis(dets: list[dict], ships: list[tuple[str, float, float]]) -> None:
    from msp import db

    db.execute("""INSERT INTO passes (pass_id, platform, start_time, stop_time, relative_orbit, orbit_direction, engine)
                  VALUES (%s, 'S1D', %s, %s, 110, 'DESCENDING', 'snap')""", (PASS, T0, T0 + timedelta(seconds=25)))
    db.copy_rows("detections", ["pass_id", "det_idx", "geom", "length_m", "peak_vv_db", "scr_db"],
                 ((PASS, i, f"POINT({d['x']} {d['y']})", d["length_m"], d["peak_vv_db"], d["scr_db"])
                  for i, d in enumerate(dets)), geom_cols={"geom": 32632})
    db.copy_rows("ais_at_pass", ["pass_id", "vessel_hash", "t_acq", "geom", "in_scene"],
                 ((PASS, h, T0.isoformat(), f"POINT({x} {y})", True) for h, x, y in ships), geom_cols={"geom": 32632})


@task
def match() -> dict:
    from msp.pipeline.ais_match import match_pass

    return match_pass(PASS)


@flow
def fixture_flow(ships):
    dets = detect_fixture()
    load_to_postgis(dets, ships)
    return dets, match()


def test_fixture_pipeline_end_to_end(isolated_db, tmp_path):
    from msp import db

    rows = np.loadtxt(FIX / "ships_rowcol.csv", delimiter=",", skiprows=1)
    with rasterio.open(FIX / "sigma0_small.tif") as src:
        tr = src.transform
    # AIS ships: three of the five painted ships, placed 60 m away from the truth, plus one far from any ship
    ships = []
    for k, (r, c, _) in enumerate(rows[:3]):
        x, y = rasterio.transform.xy(tr, r, c)
        ships.append((f"v{k:011d}", x + 60.0, y))
    x, y = rasterio.transform.xy(tr, 590, 10)
    ships.append(("vfar00000000", x, y))
    with prefect_test_harness():
        dets, res = fixture_flow(ships)
    assert len(dets) >= 5, "all painted ships must be detected"
    assert res["matched"] == 3
    m = db.query_df("SELECT vessel_hash, distance_m FROM matches ORDER BY vessel_hash")
    assert list(m.vessel_hash) == ["v00000000000", "v00000000001", "v00000000002"]
    assert (m.distance_m < 100).all()
    # the far-away AIS ship stays unmatched; the land strip produced no detections
    assert all(d["col"] < 520 for d in dets)
