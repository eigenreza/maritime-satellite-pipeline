# Data notice

Every dataset used by the pipeline, its licence, and the attribution it requires. All sources are
free to use. Derived files in this repository (the STAC catalogue, results, demo data) carry the
attributions below.

| Source | What is used | Access | Licence | Attribution |
|---|---|---|---|---|
| Copernicus Sentinel-1 (Copernicus Data Space Ecosystem) | IW GRD scenes (SAFE), VV and VH | Free account, OData API | Copernicus free, full and open data policy ([legal notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice)) | "Contains modified Copernicus Sentinel data 2025 and 2026" |
| Copernicus Sentinel-2 (Microsoft Planetary Computer) | L2A cloud-optimized GeoTIFFs: true-color and scene classification, read as windows | Anonymous STAC API with signed URLs | Copernicus legal notice (as above) | "Contains modified Copernicus Sentinel data 2026" |
| Copernicus Sentinel-3 (Copernicus Data Space Ecosystem) | SLSTR L2 sea surface temperature (SL_2_WST, GHRSST L2P NetCDF) | Free account, OData API | Copernicus legal notice (as above) | "Contains modified Copernicus Sentinel data 2026" |
| MET Norway (thredds.met.no) | MyWaveWAM 800 m wave model, daily analysis files (`mywavewam800v_currhf`) for both study periods: significant wave height, wave direction, 10 m forcing wind (NetCDF over OPeNDAP) | Anonymous | NLOD 2.0 and CC BY 4.0 ([licensing and crediting](https://www.met.no/en/free-meteorological-data/Licensing-and-crediting)) | "Data from The Norwegian Meteorological Institute" |
| ECMWF open data | IFS 0.25 degree forecasts in GRIB2: 10 m wind, mean sea level pressure, significant wave height, mean wave direction; read from the public cloud mirrors of ECMWF open data | Anonymous | CC BY 4.0 ([ECMWF licence](https://apps.ecmwf.int/datasets/licences/general)) | "Copyright European Centre for Medium-Range Weather Forecasts (ECMWF). Source www.ecmwf.int. This data is published under a Creative Commons Attribution 4.0 International (CC BY 4.0)." Data were cropped to the study area and converted to NetCDF. |
| Kystverket (Norwegian Coastal Administration), Kystdatahuset | Historical AIS positions and ship static data (type, dimensions) around each radar pass | Open endpoints of the Kystdatahuset API | NLOD ([licence](https://data.norge.no/nlod/en)) | "Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Kystverket" |
| Kartverket (Norwegian Mapping Authority) | N50 map data, Vestland county: land-cover polygons for the land mask | Geonorge download | CC BY 4.0 ([terms](https://www.kartverket.no/api-og-data/vilkar-for-bruk)) | "© Kartverket" |
| Fiskeridirektoratet (Directorate of Fisheries) | Aquaculture sites at sea (positions only) for masking fixed structures | Open map service | NLOD | "Fiskeridirektoratet" as source |
| Norwegian Offshore Directorate (Sodir) | Petroleum facilities (positions) for masking fixed structures | Open download | NLOD | "Norwegian Offshore Directorate" as source |
| OpenStreetMap (Overpass API) | Bridges, piers, breakwaters, groynes and power lines over water (line geometry and kind only) for masking fixed structures | Anonymous Overpass API | ODbL 1.0 ([copyright and licence](https://www.openstreetmap.org/copyright)) | "© OpenStreetMap contributors"; the structures layer is a derived database under ODbL |

## Privacy

AIS messages contain vessel identities. The pipeline uses MMSI numbers only internally to join
positions with ship type and dimensions. Every public file carries a salted, one-way hash per vessel
at most, plus a ship type class and a length class. No vessel name, call sign, MMSI or IMO number
is published, and a test fails the build if a number shaped like an MMSI or IMO number appears in
any public file. Open AIS data from Kystverket already leaves out fishing vessels under 15 m and
leisure craft under 45 m.

## Changes made to the data

Radar scenes were calibrated, noise-corrected, terrain-corrected, cropped and averaged to 20 m.
Optical and temperature data were cropped, mosaicked and resampled. Weather and wave fields were
cropped, sampled at points and converted between formats. All derived values are described in the
STAC catalogue (`catalog/`) with their processing lineage.
