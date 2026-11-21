# Radar processing

Sentinel-1 carries a SAR, synthetic aperture radar: a satellite radar that sees through clouds and at
night. The pipeline uses its IW GRD products (interferometric wide swath, ground range detected), which
are delivered as SAFE zip files: a folder with measurement GeoTIFFs plus XML annotation files.

The goal of processing is sigma0 (the normalized radar backscatter coefficient) for both
polarizations: VV (sent and received vertically) and VH (sent vertically, received horizontally).
Sigma0 is calibrated, so a value means the same thing in every scene. It is computed on a map grid in
UTM zone 32N (EPSG:32632) with 10 m pixels over the study box.

There are two engines. ESA SNAP is the default. A pure-Python chain takes over when SNAP fails.
`s1_process` in `src/msp/flows.py` chooses between them.

## The SNAP graph

`src/msp/graphs/s1_grd_sigma0.xml` is a SNAP processing graph. `gpt`, SNAP's command line graph
tool, fills the `${...}` parameters from `-P` options. The nodes run in this order:

| Node | Settings | Purpose |
|---|---|---|
| `Read` | `formatName` SENTINEL-1, file `${input}` | Opens the SAFE zip |
| `Apply-Orbit-File` | Sentinel Precise (Auto Download), polynomial degree 3, `continueOnFail` true | Replaces the predicted orbit with the precise orbit. Processing continues if no precise orbit is available. |
| `ThermalNoiseRemoval` | VH and VV, `removeThermalNoise` true | Subtracts the instrument noise floor, which matters over dark, calm sea |
| `Calibration` | VH and VV, `outputSigmaBand` true, `outputImageScaleInDb` false | Converts digital numbers to sigma0 on a linear scale |
| `Subset` | `geoRegion` `${region}`, `copyMetadata` true | Cuts the scene to the study box before the expensive step |
| `Terrain-Correction` | Copernicus 30m Global DEM, bilinear DEM and image resampling, `${pixelSpacing}` m, `${crs}`, `nodataValueAtSea` false | Places every pixel on the map using a DEM, a digital elevation model |
| `Write` | BEAM-DIMAP, file `${output}` | Writes the result |

`nodataValueAtSea` is false so that sea pixels keep their values. Without it, SNAP would mark pixels
where the DEM has no data as nodata, and the sea is exactly where the targets are.

`gpt_command` in `src/msp/sar/snap.py` sets the parameters from `config/pipeline.yaml`: `region` is
the study box as a WKT (well-known text) polygon, `pixelSpacing` is `area.pixel_size_m` (10), `crs` is `area.crs`
(EPSG:32632). It also sets one thread per CPU (`-q`) and a tile cache of half the memory budget (`-c`).

### From BEAM-DIMAP to GeoTIFF

BEAM-DIMAP, SNAP's native format, stores each band as an ENVI `.img` file (raw pixels with a small
text header) next to a `.dim` header. After `gpt` finishes, the script stacks the two bands with GDAL,
the standard open-source library and tool set for raster formats:

1. `gdalbuildvrt -separate` builds a virtual raster from `Sigma0_VV.img` and `Sigma0_VH.img`.
2. `gdal_translate` writes a tiled GeoTIFF with DEFLATE compression, floating-point predictor, BigTIFF
   when needed, all CPU threads, and nodata 0.

The result is the working raster `data/work/s1/<pass_id>_snap.tif`: two float32 bands, linear sigma0.

### Scene staging

`gpt_command` builds one shell script that copies the SAFE zip into a scratch folder inside the
container (`MSP_SNAP_SCRATCH`, default `/scratch`), runs `gpt` and GDAL there, copies the final
GeoTIFF back to the project folder and deletes the scratch folder. SNAP reads the zip with many small
random reads. On a host folder mounted into Docker on Windows those reads are very slow, while
container storage serves them at local disk speed.

### Two ways to reach SNAP

`MSP_SNAP_MODE` selects how `gpt` is called:

- `local`: `gpt` is on the PATH. This is the case inside the `pipeline` container, and
  `docker-compose.yml` sets it.
- `docker` (default outside the container): `docker run` starts the image named by `MSP_SNAP_IMAGE`
  (default `msp-pipeline:local`) with the project mounted at `/work`, a named volume for SNAP's
  auxiliary data (orbit files, DEM tiles) and a named volume for scratch space.

`run_snap` saves the last part of SNAP's output to `data/work/logs/snap_<pass_id>.log` and raises an
error when `gpt` exits with a nonzero code or the output file is missing.

## Pure-Python fallback

`src/msp/sar/fallback.py` (`process_scene`) produces the same working raster without SNAP, for each
polarization:

1. Reads the digital numbers (DN) of the measurement GeoTIFF straight from the zip with GDAL's
   `/vsizip/`. Only the window that covers the study box is read: `window_for_bbox` fits a
   second-order polynomial from longitude and latitude to image row and column through the ground
   control points, and adds a 200-pixel margin.
2. Reads the calibration lookup table (`calibration_lut`, the `sigmaNought` vectors) and the noise
   lookup tables from the annotation XML.
3. Builds the noise from two parts: range noise vectors (`noise_range_lut`) and, for products from IPF
   version 2.9 onward, azimuth noise blocks (`noise_azimuth_blocks`). The two are multiplied.
4. Evaluates each lookup table on a coarse grid every 16 pixels and interpolates to full resolution
   (`_lut_on_window`), which keeps memory and time low.
5. Computes `sigma0 = max(DN^2 - noise, 0) / A_sigma^2`. Pixels with DN 0 become NaN.
6. Geocodes with the ground control points (GCPs: image positions with known longitude and latitude)
   that ship inside the measurement file. `rasterio.warp.reproject` (GDAL warp) maps the array onto the
   target grid from `target_grid` with bilinear resampling.

The output is `data/work/s1/<pass_id>_python.tif`, two float32 bands named `Sigma0_VV` and
`Sigma0_VH`, tiled and DEFLATE-compressed, nodata NaN.

Differences from SNAP: no precise orbit and no DEM. The GCPs sit on the ellipsoid, so positions on land
shift with terrain height. At sea level both engines place a pixel within about one pixel of each
other, which is what ship detection needs. The engine used for each pass is stored in
`passes.engine`, in the product tag `PROCESSING_ENGINE`, and in `processing:software` and
`processing:lineage` of the product's STAC item (its entry in the standard satellite data catalog).

## Engine comparison

`run(pass_id, snap_seconds, python_seconds, factor=10)` in `src/msp/sar/compare.py` compares both
engines on the same scene:

1. Reads the VV band of both working rasters, averaged to 100 m (factor 10).
2. Reprojects the SNAP raster onto the Python grid.
3. Keeps sea pixels outside the buffered land mask where both values are positive.
4. Converts to dB and computes the median, 5th and 95th percentile of the difference (SNAP minus
   Python) and the correlation.

The numbers are written to `results/sar_engine_comparison.json` and the scatter density plot to
`docs/figures/sar_engine_comparison.png`. The README pipeline table quotes the median difference and
both run times from that file. There is no CLI command for the comparison: call the function from
Python while both working rasters exist (before `cleanup_task` removes them), passing the two run
times from the run log.

## The cataloged product

`write_db_cog` in `src/msp/pipeline/s1.py` turns the 10 m working raster into the product
`data/products/s1/<pass_id>_sigma0_db.tif`, a COG (cloud-optimized GeoTIFF: an image file that can
be read in pieces over the web):

- averages linear sigma0 to 20 m pixels (averaging in linear units, before the log),
- converts to dB (`10 * log10`),
- multiplies by 100, rounds and stores as int16, with nodata -32768,
- writes a COG with internal overviews (average resampling) through `rio-cogeo`,
- adds the tags `ACQUISITION_TIME`, `PROCESSING_ENGINE`, `BANDS` (`VV,VH`) and `UNITS` (`dB x 100`).

To read dB values, multiply the stored integers by the scale 0.01. The STAC item states this in its
raster bands (`scale` 0.01, `offset` 0, `unit` dB). Storing int16 halves the size compared with
float32 and keeps a resolution of 0.01 dB.
