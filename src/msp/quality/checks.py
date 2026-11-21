"""Quality checks per product: CRS and bounds, nodata share, value ranges, acquisition time, valid format."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
import xarray as xr
from rasterio.warp import transform_bounds

from msp import db


def _box_overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (b[2] - b[0]) * (b[3] - b[1])
    return ix * iy / area if area else 0.0


def check_cog(path: Path, crs: str, bbox: list[float], value_range: tuple[float, float],
              max_nodata: float = 0.9, scale: float = 1.0, has_time: bool = True,
              min_overlap: float = 0.95) -> list[tuple[str, bool, str]]:
    from rio_cogeo.cogeo import cog_validate

    out = []
    valid, errors, _ = cog_validate(str(path), quiet=True)
    out.append(("valid_cog", bool(valid), "; ".join(errors)[:200] or "ok"))
    with rasterio.open(path) as src:
        out.append(("crs", src.crs.to_string() == crs, src.crs.to_string()))
        bb = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        ov = _box_overlap(bb, bbox)
        out.append(("bounds_cover_area", ov >= min_overlap, f"{ov:.3f}"))
        dec = max(1, src.width // 1000)
        a = src.read(1, out_shape=(src.height // dec, src.width // dec)).astype(float)
        nod = src.nodata
        bad = ~np.isfinite(a) | ((a == nod) if nod is not None and np.isfinite(nod) else False)
        share = float(bad.mean())
        out.append(("nodata_share", share <= max_nodata, f"{share:.3f}"))
        v = a[~bad] * scale
        p1, p99 = (np.percentile(v, [1, 99]) if v.size else (np.nan, np.nan))
        ok = v.size > 0 and value_range[0] <= p1 and p99 <= value_range[1]
        out.append(("value_range", bool(ok), f"p1={p1:.2f} p99={p99:.2f}"))
        if has_time:
            tags = src.tags()
            out.append(("acquisition_time", "ACQUISITION_TIME" in tags, tags.get("ACQUISITION_TIME", "missing")))
    return out


def check_netcdf(path: Path, variables: dict[str, tuple[float, float]], need_time: bool = True) -> list:
    out = []
    try:
        ds = xr.open_dataset(path)
    except Exception as exc:
        return [("opens", False, str(exc)[:200])]
    out.append(("opens", True, "ok"))
    out.append(("cf_conventions", str(ds.attrs.get("Conventions", "")).startswith("CF"),
                ds.attrs.get("Conventions", "missing")))
    if need_time:
        out.append(("acquisition_time", "time" in ds.coords and ds.sizes.get("time", 0) > 0,
                    str(ds.sizes.get("time", 0))))
    for v, (lo, hi) in variables.items():
        if v not in ds:
            out.append((f"has_{v}", False, "missing"))
            continue
        arr = ds[v].values
        fin = arr[np.isfinite(arr)]
        out.append((f"standard_name_{v}", "standard_name" in ds[v].attrs, ds[v].attrs.get("standard_name", "")))
        if fin.size:
            p1, p99 = np.percentile(fin, [1, 99])
            out.append((f"range_{v}", bool(lo <= p1 and p99 <= hi), f"p1={p1:.2f} p99={p99:.2f}"))
        else:
            out.append((f"range_{v}", False, "no valid values"))
    ds.close()
    return out


def archive_ok(path: Path) -> bool:
    """A downloaded zip archive is complete and readable (every member passes its CRC check)."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except (zipfile.BadZipFile, OSError, EOFError):
        return False


def store(product_id: str, product_type: str, results: list[tuple[str, bool, str]]) -> dict:
    for name, ok, val in results:
        db.execute("""INSERT INTO quality_checks (product_id, product_type, check_name, passed, value)
                      VALUES (%s,%s,%s,%s,%s) ON CONFLICT (product_id, check_name)
                      DO UPDATE SET passed = EXCLUDED.passed, value = EXCLUDED.value""",
                   (product_id, product_type, name, bool(ok), str(val)))
    failed = [n for n, ok, _ in results if not ok]
    return {"checks": len(results), "failed": failed}


def check_geoparquet(path: Path, required: list[str], crs_epsg: int) -> list:
    import geopandas as gpd

    try:
        g = gpd.read_parquet(path)
    except Exception as exc:
        return [("opens", False, str(exc)[:200])]
    out = [("opens", True, "ok"), ("crs", g.crs is not None and g.crs.to_epsg() == crs_epsg, str(g.crs))]
    missing = [c for c in required if c not in g.columns]
    out.append(("columns", not missing, ", ".join(missing) or "ok"))
    out.append(("geometry_valid", bool(g.geometry.is_valid.all()), str(int((~g.geometry.is_valid).sum()))))
    return out


def run_all() -> dict:
    """Re-run the checks for every product on disk (used by `msp quality`)."""
    from msp import paths
    from msp.config import load_config

    cfg = load_config()
    crs, bbox = cfg.area.crs, list(cfg.area.bbox)
    db.execute("DELETE FROM quality_checks")
    n = 0
    for f in sorted((paths.PRODUCTS / "s1").glob("*_sigma0_db.tif")):
        store(f.name.replace("_sigma0_db.tif", ""), "sentinel-1-sigma0",
              check_cog(f, crs, bbox, (-45.0, 25.0), max_nodata=0.6, scale=0.01, min_overlap=0.6))
        n += 1
    for f in sorted((paths.PRODUCTS / "s2").glob("*_cloudmask.tif")):
        store(f.stem.replace("_cloudmask", ""), "sentinel-2-cloud", check_cog(f, crs, bbox, (0, 255), max_nodata=1.0))
        n += 1
    for f in sorted((paths.PRODUCTS / "s3").glob("S3_SST_*.nc")):
        store(f.stem, "sentinel-3-sst", check_netcdf(f, {"sst": (265.0, 300.0)}))
        n += 1
    for f in sorted((paths.PRODUCTS / "ecmwf").glob("ecmwf_ifs_*.nc")):
        store(f.stem, "ecmwf-forecast", check_netcdf(f, {"wind_speed": (0.0, 45.0), "swh": (0.0, 20.0)}))
        n += 1
    w = paths.PRODUCTS / "metocean" / "metno_waves_wind.nc"
    if w.exists():
        store("metno_waves_wind", "metno-waves", check_netcdf(w, {"hs": (0.0, 20.0), "wind_speed": (0.0, 45.0)}))
        n += 1
    for f in sorted((paths.PRODUCTS / "detections").glob("*.parquet")):
        store(f"{f.stem}_detections", "ship-detections",
              check_geoparquet(f, ["pass_id", "det_idx", "length_m", "peak_vv_db", "scr_db"], 32632))
        n += 1
    return {"products_checked": n}
