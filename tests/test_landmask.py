"""Land mask: small gaps between neighboring map polygons are filled, real sea inlets stay open."""

from shapely.geometry import Polygon

from msp.sources.landmask import SLIVER_M2, _fill_small_holes


def test_small_gaps_are_filled_and_large_holes_kept():
    outer = [(0, 0), (10_000, 0), (10_000, 10_000), (0, 10_000)]
    sliver = [(100, 100), (110, 100), (110, 110), (100, 110)]  # 100 m2: a gap between polygons
    lagoon = [(2_000, 2_000), (6_000, 2_000), (6_000, 6_000), (2_000, 6_000)]  # 16 km2 of water
    land = _fill_small_holes(Polygon(outer, [sliver, lagoon]), SLIVER_M2)
    assert len(land.interiors) == 1
    assert abs(land.area - (10_000**2 - 4_000**2)) < 1
