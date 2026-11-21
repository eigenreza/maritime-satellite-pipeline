"""CFAR ship detector on synthetic radar images (written before the detector)."""

import numpy as np
import pytest
from rasterio.transform import from_origin

from msp.detect.cfar import DetectorParams, cfar_mask, detect

PIXEL = 10.0


def sea_clutter(shape, mean=0.02, seed=0):
    """Speckled sea background: exponential intensity (single-look-like), linear sigma0."""
    rng = np.random.default_rng(seed)
    return rng.exponential(mean, size=shape).astype(np.float32)


def add_ship(img, row, col, length_px, width_px=3, angle_deg=0.0, value=2.0):
    """Paint a bright rotated rectangle (a ship) centered at row, col."""
    rr, cc = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    a = np.deg2rad(angle_deg)
    along = (cc - col) * np.cos(a) + (rr - row) * np.sin(a)
    across = -(cc - col) * np.sin(a) + (rr - row) * np.cos(a)
    m = (np.abs(along) <= length_px / 2) & (np.abs(across) <= width_px / 2)
    img[m] = value
    return img


@pytest.fixture
def params():
    return DetectorParams(guard_px=7, background_px=25, pfa_sigma=5.0, min_pixels=4, max_length_m=450,
                          merge_distance_px=3)


def test_detects_isolated_ships_and_few_false_alarms(params):
    img = sea_clutter((400, 400))
    targets = [(80, 80, 12), (200, 300, 25), (320, 150, 8)]
    for r, c, length in targets:
        add_ship(img, r, c, length)
    det = detect(img, None, np.ones(img.shape, bool), from_origin(0, 4000, PIXEL, PIXEL), params)
    assert len(det) >= 3
    for r, c, _ in targets:
        d = np.hypot(det["row"] - r, det["col"] - c).min()
        assert d < 3, f"target at {r},{c} missed"
    # Pure exponential clutter at a 5-sigma threshold: false alarms must stay rare
    assert len(det) <= 3 + 2


def test_land_is_never_detected(params):
    img = sea_clutter((300, 300))
    add_ship(img, 150, 150, 20)
    sea = np.ones(img.shape, bool)
    sea[100:200, 100:200] = False
    det = detect(img, None, sea, from_origin(0, 3000, PIXEL, PIXEL), params)
    assert det.empty or np.hypot(det["row"] - 150, det["col"] - 150).min() > 20


def test_length_estimate_for_rotated_ship(params):
    img = sea_clutter((300, 300), seed=3)
    add_ship(img, 150, 150, length_px=20, width_px=3, angle_deg=35)
    det = detect(img, None, np.ones(img.shape, bool), from_origin(0, 3000, PIXEL, PIXEL), params)
    best = det.iloc[np.argmin(np.hypot(det["row"] - 150, det["col"] - 150))]
    assert 160 <= best["length_m"] <= 240
    assert best["width_m"] < best["length_m"]


def test_brightness_and_contrast_fields(params):
    img = sea_clutter((200, 200), seed=4)
    add_ship(img, 100, 100, 10, value=1.0)
    det = detect(img, img * 0.2, np.ones(img.shape, bool), from_origin(0, 2000, PIXEL, PIXEL), params)
    best = det.iloc[np.argmin(np.hypot(det["row"] - 100, det["col"] - 100))]
    assert best["peak_vv_db"] == pytest.approx(0.0, abs=0.5)
    assert best["scr_db"] > 10
    assert np.isfinite(best["peak_vh_db"])


def test_empty_sea_gives_no_or_very_few_detections(params):
    img = sea_clutter((300, 300), seed=5)
    det = detect(img, None, np.ones(img.shape, bool), from_origin(0, 3000, PIXEL, PIXEL), params)
    assert len(det) <= 2


def test_cfar_threshold_adapts_to_rougher_sea(params):
    """A target as bright as 'calm sea x 30' is found in calm sea but not when the sea is 30x brighter."""
    calm = sea_clutter((200, 200), mean=0.01, seed=6)
    rough = sea_clutter((200, 200), mean=0.3, seed=6)
    for img in (calm, rough):
        add_ship(img, 100, 100, 6, value=0.3)
    m_calm = cfar_mask(calm, np.ones(calm.shape, bool), params)
    m_rough = cfar_mask(rough, np.ones(rough.shape, bool), params)
    assert m_calm[98:103, 97:104].any()
    assert not m_rough[98:103, 97:104].all()


def test_nan_pixels_are_ignored(params):
    img = sea_clutter((200, 200), seed=7)
    img[:, :50] = np.nan
    add_ship(img, 100, 120, 10)
    det = detect(img, None, np.ones(img.shape, bool), from_origin(0, 2000, PIXEL, PIXEL), params)
    assert np.hypot(det["row"] - 100, det["col"] - 120).min() < 3
    assert (det["col"] > 45).all()
