"""Sentinel-2 L2A from Microsoft Planetary Computer: cloud cover over sea and true-color previews.

Only windows of the cloud-optimized GeoTIFFs (COG) are read, at a reduced resolution taken from the
files' internal overviews, so no full tile is ever downloaded. The scene classification layer (SCL)
gives the cloud mask: classes 8 and 9 (cloud, medium and high probability) and 10 (thin cirrus) count
as cloud, 3 (cloud shadow) as shadow.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from msp.sar.fallback import target_grid

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
CLOUD_CLASSES = (8, 9, 10)
SHADOW_CLASS = 3
NODATA_CLASS = 0


def search_items(bbox: list[float], start: str, end: str, collection: str = "sentinel-2-l2a"):
    cat = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    items = cat.search(collections=[collection], bbox=bbox, datetime=f"{start}T00:00:00Z/{end}T23:59:59Z").item_collection()
    return [it for it in items if it.datetime and it.datetime.strftime("%Y-%m-%d") <= end]


def group_by_day(items) -> dict[str, list]:
    days = defaultdict(list)
    for it in items:
        days[it.datetime.strftime("%Y-%m-%d")].append(it)
    return dict(sorted(days.items()))


def _read_to_grid(href: str, crs: str, transform, width: int, height: int, resampling: Resampling,
                  bands=None) -> np.ndarray:
    with rasterio.open(href) as src, WarpedVRT(src, crs=crs, transform=transform, width=width, height=height,
                                                resampling=resampling, src_nodata=0, nodata=0) as vrt:
        return vrt.read(bands)


def scl_mosaic(items, bbox: list[float], crs: str, pixel: float) -> tuple[np.ndarray, object]:
    transform, width, height = target_grid(bbox, crs, pixel)
    out = np.zeros((height, width), dtype=np.uint8)
    for it in items:
        a = _read_to_grid(it.assets["SCL"].href, crs, transform, width, height, Resampling.nearest, 1)
        fill = (out == NODATA_CLASS) & (a != NODATA_CLASS)
        out[fill] = a[fill]
    return out, transform


def tci_mosaic(items, bbox: list[float], crs: str, pixel: float) -> tuple[np.ndarray, object]:
    transform, width, height = target_grid(bbox, crs, pixel)
    out = np.zeros((3, height, width), dtype=np.uint8)
    for it in items:
        a = _read_to_grid(it.assets["visual"].href, crs, transform, width, height, Resampling.average, [1, 2, 3])
        fill = (out.sum(axis=0) == 0) & (a.sum(axis=0) > 0)
        out[:, fill] = a[:, fill]
    return out, transform


def cloud_stats(scl: np.ndarray, sea: np.ndarray) -> dict:
    observed = sea & (scl != NODATA_CLASS)
    n = int(observed.sum())
    cloud = int((observed & np.isin(scl, CLOUD_CLASSES)).sum())
    shadow = int((observed & (scl == SHADOW_CLASS)).sum())
    return {
        "sea_observed_share": round(n / max(int(sea.sum()), 1), 4),
        "cloud_share": round(cloud / n, 4) if n else None,
        "shadow_share": round(shadow / n, 4) if n else None,
    }


def write_cog(path: Path, data: np.ndarray, transform, crs: str, nodata, tags: dict | None = None,
              resampling: str = "nearest") -> None:
    """Write a small array as a cloud-optimized GeoTIFF (tiled, compressed, with overviews)."""
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles

    path.parent.mkdir(parents=True, exist_ok=True)
    data = data if data.ndim == 3 else data[None]
    tmp = path.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", driver="GTiff", width=data.shape[2], height=data.shape[1], count=data.shape[0],
                       dtype=data.dtype, crs=crs, transform=transform, nodata=nodata) as dst:
        dst.write(data)
        if tags:
            dst.update_tags(**tags)
    profile = cog_profiles.get("deflate")
    cog_translate(tmp, path, profile, overview_resampling=resampling, quiet=True,
                  additional_cog_metadata=tags or None)
    tmp.unlink()


def bbox_utm(bbox: list[float], crs: str) -> tuple[float, float, float, float]:
    return transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)


def acquisition_time(items) -> datetime:
    return min(it.datetime for it in items)
