"""Pure-Python Sentinel-1 GRD processing, used when SNAP is not available.

Steps for each polarization:
1. Read the digital numbers (DN) of the measurement GeoTIFF straight from the SAFE zip (GDAL /vsizip/).
2. Thermal noise removal and radiometric calibration from the annotation lookup tables:
   sigma0 = (DN^2 - noise) / A_sigma^2, where noise = range noise x azimuth noise.
3. Geocoding with the ground control points (GCPs) shipped in the measurement file, warped by GDAL to
   the working projection and pixel size.

Differences to the SNAP chain: no precise orbit refinement and an ellipsoid-based geolocation through
GCPs instead of terrain correction with a DEM. At sea level both give the same position within about
one pixel, which is what ship detection needs.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window
from scipy.interpolate import RegularGridInterpolator

from msp.config import load_config


def _members(zf: zipfile.ZipFile, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    return sorted(n for n in zf.namelist() if rx.search(n))


def _floats(text: str | None) -> np.ndarray:
    return np.array([float(v) for v in (text or "").split()], dtype=np.float64)


def calibration_lut(xml_bytes: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (lines, pixels, sigmaNought grid) from a calibration annotation file."""
    root = ET.fromstring(xml_bytes)
    lines, pixels, values = [], None, []
    for v in root.iter("calibrationVector"):
        lines.append(int(v.findtext("line")))
        px = _floats(v.findtext("pixel"))
        sg = _floats(v.findtext("sigmaNought"))
        if pixels is None:
            pixels = px
        values.append(np.interp(pixels, px, sg))
    return np.array(lines, dtype=np.float64), pixels, np.vstack(values)


def noise_range_lut(xml_bytes: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Range noise vectors resampled onto a common pixel grid."""
    root = ET.fromstring(xml_bytes)
    tag = "noiseRangeVector" if root.find(".//noiseRangeVector") is not None else "noiseVector"
    val_tag = "noiseRangeLut" if tag == "noiseRangeVector" else "noiseLut"
    vectors = list(root.iter(tag))
    max_px = max(_floats(v.findtext("pixel")).max() for v in vectors)
    grid = np.linspace(0, max_px, 512)
    lines, values = [], []
    for v in vectors:
        px = _floats(v.findtext("pixel"))
        lut = _floats(v.findtext(val_tag))
        lines.append(int(v.findtext("line")))
        values.append(np.interp(grid, px, lut))
    return np.array(lines, dtype=np.float64), grid, np.vstack(values)


def noise_azimuth_blocks(xml_bytes: bytes) -> list[dict]:
    """Azimuth noise blocks (IPF 2.9 and later). Empty list for older products."""
    root = ET.fromstring(xml_bytes)
    blocks = []
    for v in root.iter("noiseAzimuthVector"):
        blocks.append({
            "line0": int(v.findtext("firstAzimuthLine")), "line1": int(v.findtext("lastAzimuthLine")),
            "px0": int(v.findtext("firstRangeSample")), "px1": int(v.findtext("lastRangeSample")),
            "lines": _floats(v.findtext("line")), "lut": _floats(v.findtext("noiseAzimuthLut")),
        })
    return blocks


def _grid_interp(lines: np.ndarray, pixels: np.ndarray, grid: np.ndarray, rows: np.ndarray,
                 cols: np.ndarray) -> np.ndarray:
    f = RegularGridInterpolator((lines, pixels), grid, bounds_error=False, fill_value=None)
    rr, cc = np.meshgrid(rows.astype(np.float64), cols.astype(np.float64), indexing="ij")
    return f(np.stack([rr.ravel(), cc.ravel()], axis=-1)).reshape(rr.shape)


def _lut_on_window(lines, pixels, grid, win: Window, step: int = 16) -> np.ndarray:
    """Evaluate a LUT on a coarse grid over the window, then expand to full resolution (fast)."""
    r0, c0, h, w = int(win.row_off), int(win.col_off), int(win.height), int(win.width)
    rows = np.arange(r0, r0 + h + step, step)
    cols = np.arange(c0, c0 + w + step, step)
    coarse = _grid_interp(lines, pixels, grid, rows, cols)
    fine = RegularGridInterpolator((rows, cols), coarse)
    rr = np.arange(r0, r0 + h, dtype=np.float64)
    cc = np.arange(c0, c0 + w, dtype=np.float64)
    out = np.empty((h, w), dtype=np.float32)
    for i in range(0, h, 1024):
        sub = rr[i:i + 1024]
        g = np.stack(np.meshgrid(sub, cc, indexing="ij"), axis=-1).reshape(-1, 2)
        out[i:i + len(sub)] = fine(g).reshape(len(sub), w)
    return out


def _azimuth_noise_on_window(blocks: list[dict], win: Window) -> np.ndarray | None:
    if not blocks:
        return None
    r0, c0, h, w = int(win.row_off), int(win.col_off), int(win.height), int(win.width)
    out = np.ones((h, w), dtype=np.float32)
    rows = np.arange(r0, r0 + h)
    for b in blocks:
        rs = slice(max(b["line0"], r0) - r0, min(b["line1"], r0 + h - 1) - r0 + 1)
        cs = slice(max(b["px0"], c0) - c0, min(b["px1"], c0 + w - 1) - c0 + 1)
        if rs.start >= rs.stop or cs.start >= cs.stop:
            continue
        vals = np.interp(rows[rs], b["lines"], b["lut"]) if b["lut"].size > 1 else np.full(rs.stop - rs.start, b["lut"][0])
        out[rs, cs] = vals[:, None]
    return out


def window_for_bbox(gcps: list[GroundControlPoint], bbox: list[float], shape: tuple[int, int],
                    margin: int = 200) -> Window:
    """Pixel window covering the bbox, from a least-squares fit of the GCP grid (lon, lat -> row, col)."""
    lon = np.array([g.x for g in gcps])
    lat = np.array([g.y for g in gcps])
    row = np.array([g.row for g in gcps])
    col = np.array([g.col for g in gcps])
    a = np.column_stack([np.ones_like(lon), lon, lat, lon * lat, lon**2, lat**2])
    cr, *_ = np.linalg.lstsq(a, row, rcond=None)
    cc, *_ = np.linalg.lstsq(a, col, rcond=None)
    x0, y0, x1, y1 = bbox
    xs = np.linspace(x0, x1, 21)
    ys = np.linspace(y0, y1, 21)
    gx, gy = np.meshgrid(xs, ys)
    g = np.column_stack([np.ones(gx.size), gx.ravel(), gy.ravel(), (gx * gy).ravel(), gx.ravel()**2, gy.ravel()**2])
    rr, ccv = g @ cr, g @ cc
    r0 = int(max(0, rr.min() - margin))
    r1 = int(min(shape[0], rr.max() + margin))
    c0 = int(max(0, ccv.min() - margin))
    c1 = int(min(shape[1], ccv.max() + margin))
    if r1 <= r0 or c1 <= c0:
        raise ValueError("scene does not cover the area")
    return Window(c0, r0, c1 - c0, r1 - r0)


def target_grid(bbox: list[float], crs: str, pixel: float) -> tuple[rasterio.Affine, int, int]:
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
    left, top = np.floor(left / pixel) * pixel, np.ceil(top / pixel) * pixel
    width = int(np.ceil((right - left) / pixel))
    height = int(np.ceil((top - bottom) / pixel))
    return from_origin(left, top, pixel, pixel), width, height


def process_scene(safe_zip: Path, out_tif: Path) -> dict:
    """Calibrated, noise-corrected, geocoded sigma0 (linear) for VV and VH as one GeoTIFF."""
    cfg = load_config()
    bbox, crs, pixel = cfg.area.bbox, cfg.area.crs, float(cfg.area.pixel_size_m)
    t0 = time.time()
    transform, width, height = target_grid(bbox, crs, pixel)
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    bands = {}
    with zipfile.ZipFile(safe_zip) as zf:
        for pol in ("vv", "vh"):
            tif = _members(zf, rf"measurement/.*-{pol}-.*\.tiff$")[0]
            cal = zf.read(_members(zf, rf"annotation/calibration/calibration-.*-{pol}-.*\.xml$")[0])
            noi = zf.read(_members(zf, rf"annotation/calibration/noise-.*-{pol}-.*\.xml$")[0])
            with rasterio.open(f"/vsizip/{Path(safe_zip).resolve().as_posix()}/{tif}") as src:
                gcps, _ = src.gcps
                win = window_for_bbox(gcps, bbox, (src.height, src.width))
                dn = src.read(1, window=win).astype(np.float32)
            cl, cp, cg = calibration_lut(cal)
            a_sigma = _lut_on_window(cl, cp, cg, win)
            nl, npx, ng = noise_range_lut(noi)
            noise = _lut_on_window(nl, npx, ng, win)
            az = _azimuth_noise_on_window(noise_azimuth_blocks(noi), win)
            if az is not None:
                noise *= az
            valid = dn > 0
            sigma0 = np.where(valid, np.maximum(dn * dn - noise, 0.0) / (a_sigma * a_sigma), np.nan).astype(np.float32)
            del dn, noise, a_sigma
            shifted = [GroundControlPoint(row=g.row - win.row_off, col=g.col - win.col_off, x=g.x, y=g.y, z=g.z)
                       for g in gcps]
            dst = np.full((height, width), np.nan, dtype=np.float32)
            reproject(sigma0, dst, gcps=shifted, src_crs=CRS.from_epsg(4326), dst_transform=transform,
                      dst_crs=crs, resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan,
                      num_threads=4)
            bands[pol] = dst
            del sigma0
    profile = {"driver": "GTiff", "dtype": "float32", "count": 2, "width": width, "height": height,
               "crs": crs, "transform": transform, "nodata": np.nan, "tiled": True, "blockxsize": 512,
               "blockysize": 512, "compress": "deflate", "predictor": 3, "BIGTIFF": "IF_SAFER"}
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(bands["vv"], 1)
        dst.write(bands["vh"], 2)
        dst.set_band_description(1, "Sigma0_VV")
        dst.set_band_description(2, "Sigma0_VH")
    return {"engine": "python", "seconds": round(time.time() - t0, 1), "bytes": out_tif.stat().st_size}
