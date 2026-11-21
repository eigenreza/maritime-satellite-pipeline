"""Compare the SNAP chain with the pure-Python fallback on the same scene (results/sar_engine_comparison.json)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from msp import paths
from msp.sources.landmask import sea_mask_for_grid


def run(pass_id: str, snap_seconds: float, python_seconds: float, factor: int = 10) -> dict:
    snap_p = paths.WORK / "s1" / f"{pass_id}_snap.tif"
    py_p = paths.WORK / "s1" / f"{pass_id}_python.tif"
    with rasterio.open(py_p) as p:
        a = p.read(1, out_shape=(p.height // factor, p.width // factor), resampling=Resampling.average)
        tr = p.transform * p.transform.scale(p.width / a.shape[1], p.height / a.shape[0])
        crs = p.crs
    with rasterio.open(snap_p) as s:
        b = np.full_like(a, np.nan)
        reproject(s.read(1).astype("float32"), b, src_transform=s.transform, src_crs=s.crs, dst_transform=tr,
                  dst_crs=crs, resampling=Resampling.average, src_nodata=0, dst_nodata=np.nan)
    sea = sea_mask_for_grid(paths.PRODUCTS / "landmask" / "land_buffered.shp", None, tr, a.shape[1], a.shape[0])
    m = sea & np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
    da, dbv = 10 * np.log10(a[m]), 10 * np.log10(b[m])
    d = dbv - da
    res = {"pass_id": pass_id, "compare_pixel_m": 10 * factor, "sea_pixels": int(m.sum()),
           "median_difference_db": round(float(np.median(d)), 3),
           "p5_difference_db": round(float(np.percentile(d, 5)), 2), "p95_difference_db": round(float(np.percentile(d, 95)), 2),
           "correlation_db": round(float(np.corrcoef(da, dbv)[0, 1]), 3),
           "snap_seconds": snap_seconds, "python_seconds": python_seconds}
    paths.RESULTS.mkdir(parents=True, exist_ok=True)
    (paths.RESULTS / "sar_engine_comparison.json").write_text(json.dumps(res, indent=1))
    _figure(da, dbv, paths.RESULTS.parent / "docs" / "figures" / "sar_engine_comparison.png")
    return res


def _figure(x: np.ndarray, y: np.ndarray, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(4.6, 4.2), dpi=110)
    h = ax.hist2d(x, y, bins=120, range=[[-32, 0], [-32, 0]], cmap="Blues", cmin=1)
    ax.plot([-32, 0], [-32, 0], color="#c92a2a", lw=1)
    ax.set_xlabel("Python fallback sigma0 VV (dB)")
    ax.set_ylabel("SNAP sigma0 VV (dB)")
    ax.set_title("Same scene, two processing chains (sea pixels, 100 m)", fontsize=9)
    fig.colorbar(h[3], ax=ax, label="pixels")
    fig.tight_layout()
    fig.savefig(out, metadata={"Software": None})
    plt.close(fig)
