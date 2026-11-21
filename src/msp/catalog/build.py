"""Assemble, validate and index the STAC catalogue.

Validation uses the official JSON schemas (STAC core and the extensions in use), stored in
`schemas/stac/` so that validation runs offline and gives the same answer everywhere.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pystac
from shapely.geometry import box, mapping

from msp import paths
from msp.catalog import stac
from msp.config import ROOT, load_config

SCHEMA_DIR = ROOT / "schemas" / "stac"
SCHEMA_URLS = {
    "item": "https://schemas.stacspec.org/v1.1.0/item-spec/json-schema/item.json",
    "collection": "https://schemas.stacspec.org/v1.1.0/collection-spec/json-schema/collection.json",
    "catalog": "https://schemas.stacspec.org/v1.1.0/catalog-spec/json-schema/catalog.json",
}


def _period() -> tuple[datetime, datetime]:
    """From the start of the winter window to the end of the summer period: both study windows."""
    cfg = load_config()
    start = min(cfg.period.start, (cfg.get("winter") or {}).get("start", cfg.period.start))
    return (datetime.fromisoformat(start).replace(tzinfo=UTC),
            datetime.fromisoformat(cfg.period.end).replace(hour=23, minute=59, tzinfo=UTC))


def extra_items(item_dir: Path) -> list[pystac.Item]:
    """Items for products made once for the whole period: wind and waves, reference layers, detections."""
    cfg = load_config()
    p0, p1 = _period()
    geom = mapping(box(*cfg.area.bbox))
    items = []
    waves = paths.PRODUCTS / "metocean" / "metno_waves_wind.nc"
    if waves.exists():
        items.append(stac.vector_item("metno_waves_wind", waves, None, "metocean", geom, item_dir,
                                      "application/netcdf", start=p0, end=p1,
                                      props={"msp:variables": ["hs", "wave_dir", "wind_speed", "wind_dir", "u10", "v10"],
                                             "msp:attribution": stac.LICENSES["metno"], "license": "other"}))
    ecmwf_files = sorted((paths.PRODUCTS / "ecmwf").glob("ecmwf_ifs_*.nc"))
    for f in ecmwf_files:
        run = datetime.strptime(f.stem.split("_")[-1], "%Y%m%d%H").replace(tzinfo=UTC)
        items.append(stac.vector_item(f"ecmwf_ifs_{run:%Y%m%d%H}", f, run, "metocean", geom, item_dir,
                                      "application/netcdf",
                                      props={"forecast:reference_time": run.isoformat(),
                                             "msp:source_format": "GRIB2",
                                             "msp:attribution": stac.LICENSES["ecmwf"], "license": "CC-BY-4.0"}))
    land = paths.PRODUCTS / "landmask" / "land.shp"
    if land.exists():
        items.append(stac.vector_item("land_mask", land, None, "reference-layers", geom, item_dir,
                                      "application/x-shapefile", start=p0, end=p1,
                                      props={"msp:buffer_m": float(cfg.landmask.buffer_m), "msp:static": True,
                                             "msp:attribution": stac.LICENSES["kartverket"], "license": "CC-BY-4.0"}))
    st = paths.PRODUCTS / "structures" / "structures.parquet"
    if st.exists():
        items.append(stac.vector_item("offshore_structures", st, None, "reference-layers", geom, item_dir,
                                      "application/vnd.apache.parquet", start=p0, end=p1,
                                      props={"msp:attribution": stac.LICENSES["nlod"] + "; " + stac.LICENSES["osm"],
                                             "msp:static": True, "license": "other"}))
    for f in sorted((paths.PRODUCTS / "detections").glob("*.parquet")):
        pid = f.stem
        t = datetime.strptime(pid.split("_")[1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        import geopandas as gpd
        import pandas as pd

        n = len(pd.read_parquet(f, columns=["det_idx"]))
        cols = list(gpd.read_parquet(f).columns)
        items.append(stac.vector_item(f"{pid}_detections", f, t, "ship-detections", geom, item_dir,
                                      "application/vnd.apache.parquet",
                                      props={"msp:detections": n, "msp:fields": [c for c in cols if c != "geometry"],
                                             "msp:attribution": stac.LICENSES["sentinel"], "license": "proprietary"}))
    return items


def rebuild_raster_items(item_dir: Path) -> int:
    """Recreate the Sentinel-1, -2 and -3 items from the products themselves (and PostGIS for passes)."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    from shapely import wkt

    from msp import db
    from msp.flows import _save_item
    from msp.pipeline.s1 import product_cog

    n = 0
    ps = db.query_df("""SELECT pass_id, platform, start_time, stop_time, relative_orbit, orbit_direction, engine,
                        ST_AsText(footprint) fp FROM passes""")
    for r in ps.itertuples():
        if product_cog(r.pass_id).exists():
            meta = {"pass_id": r.pass_id, "platform": r.platform, "start_time": r.start_time.to_pydatetime(),
                    "stop_time": r.stop_time.to_pydatetime(), "relative_orbit": r.relative_orbit,
                    "orbit_direction": r.orbit_direction, "footprint": wkt.loads(r.fp)}
            _save_item(stac.s1_item(meta, product_cog(r.pass_id), r.engine, item_dir))
            n += 1
    rows = [json.loads(x) for x in (paths.WORK / "s2_cloud.jsonl").read_text().splitlines() if x.strip()]
    for s in pd.DataFrame(rows).drop_duplicates("day", keep="last").itertuples():
        d = s.day.replace("-", "")
        cpath = paths.PRODUCTS / "s2" / f"S2_{d}_cloudmask.tif"
        if not cpath.exists():
            continue
        props = {"eo:cloud_cover": None if pd.isna(s.cloud_share) else round(100 * s.cloud_share, 2),
                 "msp:sea_observed_share": s.sea_observed_share,
                 "msp:cloud_share_over_sea": None if pd.isna(s.cloud_share) else s.cloud_share,
                 "constellation": "sentinel-2", "instruments": ["msi"], "license": "proprietary",
                 "msp:attribution": stac.LICENSES["sentinel"]}
        t = datetime.fromisoformat(s.time.replace("Z", "+00:00"))
        it = stac.raster_item(f"S2_{d}_cloud", cpath, t, "sentinel-2-cloud", item_dir,
                              bands=[{"name": "cloud", "description": "1 cloud, 0 clear, 255 not observed"}], extra=props)
        it.stac_extensions.append("https://stac-extensions.github.io/eo/v1.1.0/schema.json")
        tc = paths.PRODUCTS / "s2" / f"S2_{d}_truecolor.tif"
        if tc.exists():
            it.add_asset("truecolor", pystac.Asset(href=stac._rel(tc, item_dir), media_type=pystac.MediaType.COG,
                                                   roles=["visual"], title="True-color preview"))
        _save_item(it)
        n += 1
    for nc in sorted((paths.PRODUCTS / "s3").glob("S3_SST_*.nc")):
        d = nc.stem.split("_")[-1]
        prev = paths.PRODUCTS / "s3" / f"S3_SST_{d}_celsius.tif"
        with xr.open_dataset(nc) as ds:
            t = pd.Timestamp(ds["time"].values[0]).to_pydatetime().replace(tzinfo=UTC)
            valid = float(np.isfinite(ds["sst"].values).mean())
        it = stac.raster_item(f"S3_SST_{d}", prev, t, "sentinel-3-sst", item_dir, bands=[{"name": "sst", "unit": "degC"}],
                              extra={"constellation": "sentinel-3", "instruments": ["slstr"], "license": "proprietary",
                                     "msp:attribution": stac.LICENSES["sentinel"], "msp:valid_share": round(valid, 4)})
        it.assets["data"].roles = ["visual"]
        it.add_asset("netcdf", pystac.Asset(href=stac._rel(nc, item_dir), media_type="application/netcdf",
                                            roles=["data"], title="CF NetCDF (Kelvin)"))
        _save_item(it)
        n += 1
    return n


def build_all() -> dict:
    cfg = load_config()
    item_dir = paths.WORK / "stac_items"
    if item_dir.exists():
        import shutil

        shutil.rmtree(item_dir)
    rebuild_raster_items(item_dir)
    items = [pystac.Item.from_file(str(p)) for p in sorted(item_dir.glob("*.json"))]
    for it in items:
        # Asset hrefs were written relative to the item folder; make them absolute before re-homing
        for a in it.assets.values():
            if not a.href.startswith(("http", "/")) and ":" not in a.href[:3]:
                a.href = str((item_dir / a.href).resolve())
    extra = extra_items(item_dir)
    for it in extra:
        for a in it.assets.values():
            if not Path(a.href).is_absolute():
                a.href = str((item_dir / a.href).resolve())
    root = paths.CATALOG
    if root.exists():
        import shutil

        shutil.rmtree(root)
    cat = stac.build_catalog(items + extra, root, _period(), list(cfg.area.bbox))
    cat.make_all_asset_hrefs_relative()
    stac.save(cat)
    n_valid, errors = validate(root)
    rows = stac.item_index(cat)
    stac.write_index(rows, root / "items.parquet")
    (paths.RESULTS).mkdir(parents=True, exist_ok=True)
    summary = {"items": len(rows), "collections": sorted({r["collection"] for r in rows}),
               "valid": n_valid, "invalid": len(errors)}
    (paths.RESULTS / "catalogue_summary.json").write_text(json.dumps(summary, indent=1))
    return summary | {"errors": errors[:5]}


def validate(root: Path) -> tuple[int, list[str]]:
    """Validate every STAC file against the stored schemas (core + extensions)."""
    from msp.catalog.validate import validate_file

    files = sorted(root.rglob("*.json"))
    errors = []
    for f in files:
        errors += [f"{f.relative_to(root).as_posix()}: {e}" for e in validate_file(f)]
    return len(files) - len({e.split(':')[0] for e in errors}), errors
