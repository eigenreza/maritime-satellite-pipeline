"""Readers and writers for every format the pipeline handles, on small fixture files."""

from datetime import UTC, datetime

import geopandas as gpd
import numpy as np
import rasterio
import xarray as xr

from msp.quality import checks
from msp.sources.ecmwf import read_grib, to_cf
from msp.sources.landmask import sea_mask_for_grid
from msp.sources.sentinel2 import write_cog

BBOX = [4.6, 60.6, 5.0, 60.8]


def test_cog_write_and_quality_checks(fix, tmp_path):
    with rasterio.open(fix / "sigma0_small.tif") as src:
        db = (10 * np.log10(src.read()) * 100).astype("int16")
        tr, crs = src.transform, src.crs.to_string()
    out = tmp_path / "sigma0_db.tif"
    write_cog(out, db, tr, crs, -32768, {"ACQUISITION_TIME": "2026-08-06T05:55:26Z"})
    res = {n: ok for n, ok, _ in checks.check_cog(out, crs, [4.83, 60.77, 4.95, 60.83], (-45, 25), scale=0.01)}
    assert res["valid_cog"] and res["crs"] and res["value_range"] and res["acquisition_time"]


def test_netcdf_is_cf_and_has_from_directions(fix):
    ds = xr.open_dataset(fix / "waves_small.nc")
    assert ds.attrs["Conventions"].startswith("CF")
    assert ds["hs"].attrs["standard_name"] == "sea_surface_wave_significant_height"
    # the model gives the direction waves travel TO (90 = east); the CF output is the FROM direction
    assert float(ds["wave_dir"].isel(time=0, rlat=0, rlon=0)) == 270.0
    # wind blowing TO 270 (west) comes FROM 90 (east): the eastward component is negative
    assert float(ds["u10"].isel(time=0, rlat=0, rlon=0)) < 0
    res = {n: ok for n, ok, _ in checks.check_netcdf(fix / "waves_small.nc", {"hs": (0, 15)})}
    assert res["opens"] and res["cf_conventions"] and res["range_hs"]


def test_netcdf_is_hdf5_based(fix):
    import h5py

    with h5py.File(fix / "waves_small.nc", "r") as f:
        assert "hs" in f


def test_grib2_read_and_crop(fix):
    ds = to_cf(read_grib(fix / "wind_small.grib2", BBOX, margin=0.3))
    assert set(ds.data_vars) >= {"u10", "v10", "wind_speed"}
    assert np.allclose(ds["wind_speed"].values, 5.0)
    assert ds["u10"].attrs["standard_name"] == "eastward_wind"


def test_shapefile_land_mask(fix, monkeypatch):
    with rasterio.open(fix / "sigma0_small.tif") as src:
        sea = sea_mask_for_grid(fix / "land_small.shp", None, src.transform, src.width, src.height)
    assert sea[:, :500].mean() > 0.95
    assert not sea[:, 530:].any()


def test_geoparquet_round_trip(fix, tmp_path):
    gdf = gpd.read_parquet(fix / "detections_small.parquet")
    assert gdf.crs.to_epsg() == 32632 and len(gdf) == 2
    gdf.to_crs(4326).to_parquet(tmp_path / "x.parquet")
    back = gpd.read_parquet(tmp_path / "x.parquet")
    assert back.crs.to_epsg() == 4326
    import pyarrow.parquet as pq

    assert b"geo" in pq.read_schema(tmp_path / "x.parquet").metadata


def test_fallback_lut_parsers():
    from msp.sar.fallback import calibration_lut, noise_range_lut

    cal = (b"<calibration><calibrationVectorList>"
           b"<calibrationVector><line>0</line><pixel>0 100</pixel><sigmaNought>500 520</sigmaNought></calibrationVector>"
           b"<calibrationVector><line>50</line><pixel>0 100</pixel><sigmaNought>510 530</sigmaNought></calibrationVector>"
           b"</calibrationVectorList></calibration>")
    lines, pix, grid = calibration_lut(cal)
    assert list(lines) == [0, 50] and grid.shape == (2, 2) and grid[1, 1] == 530
    noise = (b"<noise><noiseRangeVectorList>"
             b"<noiseRangeVector><line>0</line><pixel>0 100</pixel><noiseRangeLut>10 20</noiseRangeLut></noiseRangeVector>"
             b"</noiseRangeVectorList></noise>")
    nl, npx, ng = noise_range_lut(noise)
    assert ng.shape[0] == 1 and np.isclose(ng[0, 0], 10) and np.isclose(ng[0, -1], 20)


def test_ecmwf_replay_publication_rule():
    from msp.sources.ecmwf import latest_published_run

    assert latest_published_run(datetime(2026, 8, 6, 12, tzinfo=UTC)) == datetime(2026, 8, 6, 0, tzinfo=UTC)
    assert latest_published_run(datetime(2026, 8, 6, 7, tzinfo=UTC)) == datetime(2026, 8, 5, 12, tzinfo=UTC)
    assert latest_published_run(datetime(2026, 8, 6, 21, tzinfo=UTC)) == datetime(2026, 8, 6, 12, tzinfo=UTC)
