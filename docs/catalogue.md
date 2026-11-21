# STAC catalog

STAC, SpatioTemporal Asset Catalog: a standard catalog format for satellite data, written as JSON. An
item describes one product: where it is, when it was acquired and which files (assets) belong to it.
Items are grouped into collections. Every derived product of the pipeline gets an item, so the whole
output can be searched by place, time and type.

The code is in `src/msp/catalog/`: `stac.py` builds items and collections, `build.py` assembles the
catalog, `validate.py` checks it against the official schemas.

Formats named below: COG, cloud-optimized GeoTIFF (an image file that can be read in pieces over the
web). GeoParquet, a columnar table file with a geometry column. CF NetCDF, a gridded data file that
follows the Climate and Forecast conventions (see below). Sigma0 is calibrated radar backscatter, and
CFAR (constant false alarm rate) is the detector that compares each pixel with its local sea background.

## Collections

`COLLECTIONS` in `src/msp/catalog/stac.py`:

| Collection | Title | Content |
|---|---|---|
| `sentinel-1-sigma0` | Sentinel-1 calibrated radar backscatter | Calibrated, noise-corrected, terrain-corrected IW GRD sigma0 (VV and VH), 20 m COG |
| `ship-detections` | Radar ship detections | CFAR detections per pass with shape, brightness, AIS match, sea state, ship probability and estimated length (GeoParquet) |
| `sentinel-2-cloud` | Sentinel-2 cloud cover over sea | Cloud mask from the L2A scene classification and a true-color preview per acquisition day |
| `sentinel-3-sst` | Sentinel-3 sea surface temperature | SLSTR L2 sea surface temperature on a regular grid, CF NetCDF with a COG preview |
| `metocean` | Wind and waves | MET Norway wave model fields and ECMWF forecasts converted from GRIB2, both NetCDF |
| `reference-layers` | Reference layers | Land mask (Shapefile) and offshore structures (GeoParquet) |

Each collection has the study box as its spatial extent, the span of both study periods as its temporal extent, a
processor entry under `providers` and an attribution string in `msp:attribution`.

## Items per product type

**Sentinel-1 sigma0** (`s1_item`): ID `<pass_id>_sigma0`, `datetime`, `start_datetime` and
`end_datetime` from the SAFE manifest. One asset `data` (the COG). Extensions:

- projection: `proj:epsg` (32632), `proj:shape`, `proj:transform`, `proj:bbox`,
- raster: two bands, `VV` and `VH`, with `scale` 0.01, `offset` 0, `unit` dB, nodata and data type
  int16,
- SAR: instrument mode IW, C band, center frequency 5.405 GHz, polarizations VV and VH, product type
  GRD, resolution and pixel spacing, number of looks,
- sat: `sat:relative_orbit` and `sat:orbit_state`,
- processing: `processing:level` L2, `processing:software` (ESA SNAP 14.0, or the GDAL version for the
  Python fallback, plus the pipeline version) and `processing:lineage` in plain words.

**Sentinel-2 cloud** (`s2_day_task` in `src/msp/flows.py`): ID `S2_<YYYYMMDD>_cloud`. Asset `data` is
the cloud mask COG (1 cloud, 0 clear, 255 not observed) with a raster band. A `truecolor` asset with
role `visual` is added when the cloud share over sea is below 0.6. Properties include
`eo:cloud_cover`, `msp:sea_observed_share` and `msp:cloud_share_over_sea`. Extensions: projection,
raster, eo (electro-optical).

**Sentinel-3 SST** (`s3_day_task`): ID `S3_SST_<YYYYMMDD>`. Asset `data` (role `visual`) is the COG
preview in degrees Celsius, asset `netcdf` (role `data`) the CF NetCDF in Kelvin. Property
`msp:valid_share` gives the share of grid cells with a valid temperature. Extensions: projection,
raster.

**Ship detections** (`extra_items` in `src/msp/catalog/build.py`): ID `<pass_id>_detections`, one
GeoParquet asset, `msp:detections` (row count) and `msp:fields` (column names).

**Metocean** (`extra_items`): `metno_waves_wind` spans both study periods and lists its variables in
`msp:variables`. One item per ECMWF run, `ecmwf_ifs_<YYYYMMDDHH>`, carries the run time as `datetime`
and `forecast:reference_time`, and `msp:source_format` GRIB2.

**Reference layers** (`extra_items`): `land_mask` (Shapefile, `msp:buffer_m`) and
`offshore_structures` (GeoParquet: aquaculture sites, petroleum facilities and, from OpenStreetMap,
bridges, piers, breakwaters, groynes and power lines over water). They are static layers: they have no
single acquisition time, so `datetime` is null and `start_datetime` and `end_datetime` cover both study
periods (1 December 2025 to 30 September 2026), with `msp:static` true. The demo catalogue shows them
as "static layer".

Items made once for the whole period use the study box as their geometry. Raster items take their
geometry from the file bounds, transformed to longitude and latitude.

## Building the catalog

Flows write one item file per product to `data/work/stac_items/`, with asset paths relative to that
folder. `msp catalogue` (`build_all`) then:

1. reads every item file and turns asset paths into absolute paths,
2. adds the items from `extra_items` for products made outside the per-product tasks,
3. deletes `catalog/` and builds a fresh catalog with one child collection per collection ID,
4. makes all asset links relative and saves the catalog as self-contained (`pystac.CatalogType.SELF_CONTAINED`),
5. validates every JSON file, writes the GeoParquet index and `results/catalogue_summary.json` (item
   count, collections, valid and invalid files).

## Offline validation

`src/msp/catalog/validate.py` validates against the official JSON schemas for STAC 1.1.0 (item,
collection, catalog) and for every extension URL listed in an object's `stac_extensions`. The schemas
are stored in `schemas/stac/`, one file per URL with `/` replaced by `__`. `fetch_schemas()` downloads
a schema and everything it references once. After that, `_registry()` resolves every `$ref` from the
local files, so validation needs no network and gives the same answer everywhere. A schema missing from
`schemas/stac/` is reported as an error, not fetched.

## GeoParquet index

`catalog/items.parquet` holds one row per item: `id`, `collection`, `datetime` (ISO text, the start
time for items with a time range), `bbox`, `properties` (JSON text) and the item geometry in EPSG:4326.
GeoParquet is Parquet with standard geometry metadata, so GeoPandas, DuckDB and QGIS read it directly.
The demo's catalog page uses a similar flat list (`export_catalogue` in `src/msp/demo/export.py`).

## CF conventions for NetCDF outputs

CF (Climate and Forecast) conventions define standard variable names, units and coordinates for NetCDF.
All NetCDF outputs set `Conventions = "CF-1.8"`:

| File | Variables and standard names | Notes |
|---|---|---|
| `data/products/s3/S3_SST_<YYYYMMDD>.nc` | `sst`: `sea_surface_skin_temperature`, K | Regular 0.01 degree `lat`, `lon` grid, `time` |
| `data/products/metocean/metno_waves_wind.nc` | `hs`, `wave_dir`, `wind_speed`, `wind_dir`, `u10`, `v10` | Rotated pole grid (`rotated_pole` grid mapping) with 2D `lat` and `lon`, directions converted to "from" |
| `data/products/ecmwf/ecmwf_ifs_<YYYYMMDDHH>.nc` | `u10`, `v10`, `wind_speed`, `msl`, `swh`, `mwd` | `time` is the forecast valid time, attribute `forecast_reference_time` |

The quality checks in `src/msp/quality/checks.py` verify the `Conventions` attribute and a
`standard_name` on each checked variable.

## Searching with pystac

Open the catalog and filter by collection and properties:

```python
import pystac

cat = pystac.Catalog.from_file("catalog/catalog.json")
s1 = cat.get_child("sentinel-1-sigma0")
descending = [it for it in s1.get_items()
              if it.properties.get("sat:orbit_state") == "descending"]
for it in descending:
    print(it.id, it.datetime, it.assets["data"].get_absolute_href())
```

Filter by time with the item datetimes:

```python
in_window = [it for it in cat.get_items(recursive=True)
             if it.datetime and start <= it.datetime <= end]
```

For spatial queries the GeoParquet index is faster than walking the JSON files:

```python
import geopandas as gpd
from shapely.geometry import box

idx = gpd.read_parquet("catalog/items.parquet")
near_fedje = idx[(idx.collection == "ship-detections") & idx.intersects(box(4.6, 60.7, 5.0, 60.9))]
```

Asset links are relative to each item file, so the catalog works after the project folder moves.
