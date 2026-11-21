"""CFAR ship detector for calibrated SAR images.

CFAR (constant false alarm rate) compares every sea pixel with the statistics of the sea around it:
a ring of background pixels outside a guard window (the guard window keeps the ship itself out of
its own background). A pixel is a candidate when

    sigma0 > mean_background + k * std_background

so the threshold rises automatically when the sea is rough and bright. Candidate pixels are grouped
into objects; each object gets a position, a length and width from its pixel cloud, and brightness
values in dB.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from rasterio.transform import Affine
from rasterio.transform import xy as pix2xy
from scipy import ndimage


@dataclass(frozen=True)
class DetectorParams:
    guard_px: int = 7
    background_px: int = 25
    pfa_sigma: float = 5.0
    min_pixels: int = 4
    max_length_m: float = 450.0
    merge_distance_px: int = 3
    min_fill: float = 0.3   # share of the object's outline (length x width) covered by bright pixels
    # second stage (applied to stored candidates, tuned on the development passes)
    min_scr_db: float = 0.0
    min_peak_db: float = -99.0

    @classmethod
    def from_config(cls, cfg: dict) -> DetectorParams:
        return cls(**{k: cfg[k] for k in cls.__dataclass_fields__ if k in cfg})


def _box_sum(a: np.ndarray, size: int) -> np.ndarray:
    return ndimage.uniform_filter(a, size=size, mode="constant", cval=0.0) * float(size * size)


def cfar_mask(img: np.ndarray, sea: np.ndarray, p: DetectorParams) -> np.ndarray:
    """Boolean mask of pixels brighter than their local sea background (cell-averaging CFAR)."""
    valid = sea & np.isfinite(img)
    x = np.where(valid, img, 0.0).astype(np.float64)
    m = valid.astype(np.float64)
    g = 2 * p.guard_px + 1
    b = 2 * p.background_px + 1
    n = _box_sum(m, b) - _box_sum(m, g)
    s1 = _box_sum(x, b) - _box_sum(x, g)
    s2 = _box_sum(x * x, b) - _box_sum(x * x, g)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s1 / n
        var = np.maximum(s2 / n - mean * mean, 0.0)
        thr = mean + p.pfa_sigma * np.sqrt(var)
        enough = n >= 0.25 * (b * b - g * g)
        return valid & enough & (x > thr)


def _cfar_tiled(img, sea, p: DetectorParams, tile: int = 2048):
    """Run cfar_mask on overlapping tiles to keep memory small on full scenes."""
    h, w = img.shape
    if h * w <= tile * tile * 1.5:
        return cfar_mask(img, sea, p)
    pad = p.background_px + 2
    out = np.zeros((h, w), dtype=bool)
    for r0 in range(0, h, tile):
        for c0 in range(0, w, tile):
            r1, c1 = min(h, r0 + tile), min(w, c0 + tile)
            ra, ca = max(0, r0 - pad), max(0, c0 - pad)
            rb, cb = min(h, r1 + pad), min(w, c1 + pad)
            sub_sea = sea[ra:rb, ca:cb]
            if not sub_sea.any():
                continue
            m = cfar_mask(img[ra:rb, ca:cb], sub_sea, p)
            out[r0:r1, c0:c1] = m[r0 - ra:r1 - ra, c0 - ca:c1 - ca]
    return out


def _db(v: float) -> float:
    return float(10.0 * np.log10(v)) if v > 0 and np.isfinite(v) else float("nan")


def detect(vv: np.ndarray, vh: np.ndarray | None, sea: np.ndarray, transform: Affine,
           p: DetectorParams, pixel_m: float | None = None) -> pd.DataFrame:
    """Detect bright objects at sea. Returns one row per object with map position and features."""
    pixel_m = pixel_m or abs(transform.a)
    mask = _cfar_tiled(vv, sea, p)
    grouped = ndimage.binary_dilation(mask, iterations=p.merge_distance_px) if p.merge_distance_px else mask
    labels, n = ndimage.label(grouped, structure=np.ones((3, 3), bool))
    labels = np.where(mask, labels, 0)
    cols = ["row", "col", "x", "y", "n_pixels", "fill", "length_m", "width_m", "heading_deg", "peak_vv_db",
            "mean_vv_db", "peak_vh_db", "bg_vv_db", "scr_db"]
    if n == 0:
        return pd.DataFrame(columns=cols)
    objects = ndimage.find_objects(labels)
    rows = []
    halo = p.background_px
    for i, sl in enumerate(objects, start=1):
        if sl is None:
            continue
        sub = labels[sl] == i
        npx = int(sub.sum())
        if npx < p.min_pixels:
            continue
        rr, cc = np.nonzero(sub)
        rr = rr + sl[0].start
        cc = cc + sl[1].start
        vals = vv[rr, cc].astype(np.float64)
        wgt = np.clip(vals, 0, None)
        wsum = wgt.sum() if wgt.sum() > 0 else 1.0
        r_c = float((rr * wgt).sum() / wsum)
        c_c = float((cc * wgt).sum() / wsum)
        # Shape from the pixel cloud: principal axes give orientation, extent gives length and width
        if npx >= 3:
            coords = np.column_stack([cc - cc.mean(), rr - rr.mean()]).astype(np.float64)
            cov = np.cov(coords.T)
            evals, evecs = np.linalg.eigh(cov)
            major = evecs[:, 1]
            along = coords @ major
            across = coords @ evecs[:, 0]
            length = (along.max() - along.min() + 1.0) * pixel_m
            width = (across.max() - across.min() + 1.0) * pixel_m
            heading = float((np.degrees(np.arctan2(major[0], -major[1])) + 360.0) % 180.0)
        else:
            length = width = np.sqrt(npx) * pixel_m
            heading = float("nan")
        if length > p.max_length_m:
            continue
        fill = npx / max((length / pixel_m) * (width / pixel_m), 1.0)
        if fill < p.min_fill:
            continue
        # Local sea background in a ring around the object
        r0, r1 = max(0, sl[0].start - halo), min(vv.shape[0], sl[0].stop + halo)
        c0, c1 = max(0, sl[1].start - halo), min(vv.shape[1], sl[1].stop + halo)
        ring = vv[r0:r1, c0:c1]
        ring_ok = sea[r0:r1, c0:c1] & np.isfinite(ring) & ~mask[r0:r1, c0:c1]
        bg = float(np.median(ring[ring_ok])) if ring_ok.sum() > 10 else float("nan")
        peak = float(np.nanmax(vals))
        x, y = pix2xy(transform, r_c, c_c, offset="center")
        rows.append({
            "row": r_c, "col": c_c, "x": float(x), "y": float(y), "n_pixels": npx, "fill": round(float(fill), 3),
            "length_m": float(length), "width_m": float(width), "heading_deg": heading,
            "peak_vv_db": _db(peak), "mean_vv_db": _db(float(np.nanmean(vals))),
            "peak_vh_db": _db(float(np.nanmax(vh[rr, cc]))) if vh is not None else float("nan"),
            "bg_vv_db": _db(bg), "scr_db": _db(peak) - _db(bg) if np.isfinite(bg) else float("nan"),
        })
    return pd.DataFrame(rows, columns=cols)
