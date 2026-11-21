"""Create the small fixture files used by the tests (run once; the outputs are committed).

    python tests/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import xarray as xr
from rasterio.transform import from_origin
from shapely.geometry import Polygon

FIX = Path(__file__).resolve().parent / "fixtures"

# A 6 km x 6 km box of sea west of Fedje (UTM 32N), 10 m pixels
X0, Y0, PIX, N = 270000.0, 6740000.0, 10.0, 600


def sigma0_cog() -> None:
    """Synthetic calibrated radar image: speckled sea, a strip of land, five ships."""
    rng = np.random.default_rng(42)
    vv = rng.gamma(4.4, 0.02 / 4.4, size=(N, N)).astype(np.float32)   # multi-look sea speckle, mean -17 dB
    vh = (vv * 0.15).astype(np.float32)
    vv[:, 520:] = rng.gamma(4.4, 0.3 / 4.4, size=(N, 80))               # land on the east side (bright)
    ships = [(100, 100, 12, 0), (300, 200, 25, 30), (450, 120, 6, 90), (200, 420, 18, 60), (520, 380, 9, 10)]
    rr, cc = np.mgrid[0:N, 0:N]
    for r, c, length, ang in ships:
        a = np.deg2rad(ang)
        along = (cc - c) * np.cos(a) + (rr - r) * np.sin(a)
        across = -(cc - c) * np.sin(a) + (rr - r) * np.cos(a)
        m = (np.abs(along) <= length / 2) & (np.abs(across) <= 1.5)
        vv[m] = 1.5
        vh[m] = 0.2
    profile = {"driver": "GTiff", "dtype": "float32", "count": 2, "width": N, "height": N, "crs": "EPSG:32632",
               "transform": from_origin(X0, Y0 + N * PIX, PIX, PIX), "nodata": np.nan, "tiled": True,
               "blockxsize": 256, "blockysize": 256, "compress": "deflate"}
    with rasterio.open(FIX / "sigma0_small.tif", "w", **profile) as dst:
        dst.write(vv, 1)
        dst.write(vh, 2)
    np.savetxt(FIX / "ships_rowcol.csv", np.array(ships)[:, :3], fmt="%d", delimiter=",", header="row,col,length_px",
               comments="")


def land_shapefile() -> None:
    x_land = X0 + 520 * PIX
    poly = Polygon([(x_land, Y0), (X0 + N * PIX, Y0), (X0 + N * PIX, Y0 + N * PIX), (x_land, Y0 + N * PIX)])
    gpd.GeoDataFrame({"kind": ["land"]}, geometry=[poly], crs="EPSG:32632").to_file(FIX / "land_small.shp")


def detections_geoparquet() -> None:
    gdf = gpd.GeoDataFrame({"det_idx": [0, 1], "length_m": [120.0, 60.0], "peak_vv_db": [1.8, -3.0]},
                           geometry=gpd.points_from_xy([X0 + 1000, X0 + 2000], [Y0 + 5000, Y0 + 3000]), crs="EPSG:32632")
    gdf.to_parquet(FIX / "detections_small.parquet")


def waves_netcdf() -> None:
    t = np.array(["2026-08-06T00:00", "2026-08-06T01:00", "2026-08-06T02:00"], dtype="datetime64[ns]")
    lat = np.linspace(60.6, 60.8, 5)[:, None] * np.ones((5, 6))
    lon = np.linspace(4.6, 5.0, 6)[None, :] * np.ones((5, 6))
    hs = np.stack([np.full((5, 6), 1.0 + k * 0.5) for k in range(3)]).astype(np.float32)
    hs[:, :, -1] = np.nan  # a land column
    ds = xr.Dataset({"hs": (("time", "rlat", "rlon"), hs), "thq": (("time", "rlat", "rlon"), np.full_like(hs, 90.0)),
                     "ff": (("time", "rlat", "rlon"), np.full_like(hs, 8.0)), "dd": (("time", "rlat", "rlon"), np.full_like(hs, 270.0))},
                    coords={"time": t, "rlat": np.arange(5.0), "rlon": np.arange(6.0)})
    from msp.sources.metno import write_cf

    write_cf(ds, lat, lon, FIX / "waves_small.nc")


def tiny_grib2() -> None:
    """Two GRIB2 messages (10 m u and v wind) on a 3 x 4 regular grid, written with ecCodes."""
    import eccodes

    with open(FIX / "wind_small.grib2", "wb") as f:
        for short, value in (("10u", 3.0), ("10v", -4.0)):
            gid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            for k, v in (("Ni", 4), ("Nj", 3), ("latitudeOfFirstGridPointInDegrees", 61.0),
                         ("longitudeOfFirstGridPointInDegrees", 4.0), ("latitudeOfLastGridPointInDegrees", 60.5),
                         ("longitudeOfLastGridPointInDegrees", 4.75), ("iDirectionIncrementInDegrees", 0.25),
                         ("jDirectionIncrementInDegrees", 0.25), ("dataDate", 20260806), ("dataTime", 0)):
                eccodes.codes_set(gid, k, v)
            eccodes.codes_set(gid, "shortName", short)
            eccodes.codes_set(gid, "typeOfFirstFixedSurface", 103)
            eccodes.codes_set(gid, "scaledValueOfFirstFixedSurface", 10)
            eccodes.codes_set_values(gid, np.full(12, value))
            eccodes.codes_write(gid, f)
            eccodes.codes_release(gid)


if __name__ == "__main__":
    FIX.mkdir(exist_ok=True)
    sigma0_cog()
    land_shapefile()
    detections_geoparquet()
    waves_netcdf()
    tiny_grib2()
    print(sorted(p.name for p in FIX.iterdir()))
