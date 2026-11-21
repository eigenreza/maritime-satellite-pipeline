import { Intro, REPO_URL, Tour, useTour, type TourStep } from '../components/ui'

const STEPS: TourStep[] = [
  { target: '#arch', title: 'Architecture', text: 'Where each part runs: the full pipeline on a laptop in Linux containers, this website on a small free web server.' },
  { target: '#formats', title: 'Data formats', text: 'Each file format the pipeline reads or writes, and where it is used.' },
  { target: '#docs', title: 'Read more', text: 'Links to the detailed documentation in the repository.' },
]

const FORMATS = [
  ['SAFE (zip)', 'Sentinel-1 radar scenes as delivered: images plus calibration tables and metadata', 'Sentinel-1 download, SNAP input'],
  ['GeoTIFF and COG', 'Georeferenced raster images. COG, cloud-optimized GeoTIFF: an image file that can be read in pieces over the web', 'Radar backscatter products, cloud masks, true-color and temperature previews; Sentinel-2 is read as COG windows'],
  ['NetCDF (HDF5)', 'Self-describing arrays with metadata; NetCDF-4 files are stored in HDF5', 'Wave and wind fields (MET Norway), sea surface temperature (Sentinel-3), forecasts after conversion; outputs follow CF conventions'],
  ['GRIB2', 'The compact binary format of weather centers', 'ECMWF forecasts: 10 m wind, pressure, wave height and direction, read with cfgrib'],
  ['Shapefile', 'Classic vector format for map features', 'Land mask built from the Kartverket coastline, buffered to keep detections at sea'],
  ['GeoParquet', 'Columnar table format with geometry, fast to query', 'Ship detections per pass, offshore structures, catalogue index'],
  ['STAC JSON', 'STAC: a standard catalogue format for satellite data', 'One record per product, validated against the official schemas'],
  ['PostGIS (SQL)', 'A spatial database', 'Detections, AIS positions, matches, run log, quality checks, freshness'],
]

const TOOLS = [
  ['Prefect 3', 'orchestration: flows per source, retries, caching, concurrency limits, schedules, backfill, run history'],
  ['ESA SNAP 14', 'radar processing graph run from Python (orbit, noise removal, calibration, terrain correction)'],
  ['GDAL, Rasterio', 'reading, warping and writing rasters; Python fallback for radar geocoding'],
  ['Xarray, cfgrib, h5netcdf', 'NetCDF, HDF5 and GRIB2 arrays'],
  ['PostGIS, psycopg', 'spatial SQL for matching and aggregation'],
  ['pystac, JSON Schema', 'catalogue building and validation'],
  ['PyTorch, scikit-learn', 'the convolutional network, the gradient boosting baseline, calibration'],
  ['Docker Compose', 'PostGIS, Prefect server and the pipeline worker (Python + SNAP) as Linux containers'],
  ['GitHub Actions, Bicep, Azure', 'tests and checks on every change; infrastructure as code for this free web server'],
  ['React, TypeScript, MapLibre', 'this website'],
]

const DOCS = [
  ['architecture.md', 'Architecture'], ['pipeline.md', 'Flows, retries, caching, schedules, backfill'], ['sar-processing.md', 'SNAP graph and the Python fallback'],
  ['detection-and-matching.md', 'Ship detection and AIS matching'], ['ship-classifier.md', 'The AI: labels, model, calibration, evaluation'],
  ['catalogue.md', 'STAC catalogue and metadata'], ['operations.md', 'Freshness, quality checks, alerts, runbook'], ['evaluation.md', 'Evaluation rules'],
]

export default function Built() {
  const tour = useTour('built')
  return (
    <main className="page">
      <Intro
        what={<>How the project is put together: where things run, which data formats are used where, and which tools do the work.</>}
        how={<>Scroll through the diagram and the two tables. Follow the links at the bottom for the full documentation.</>}
        notice={<>The heavy work runs locally in Linux containers. This website only serves precomputed results, so it needs no database and no keys.</>}
      />
      <section className="card" id="arch">
        <h2>Architecture</h2>
        <svg viewBox="0 0 760 250" width="100%" role="img" aria-label="Architecture diagram">
          <rect x="10" y="10" width="150" height="230" rx="12" fill="#f1f6fa" stroke="#c9d6df" />
          <text x="85" y="32" textAnchor="middle" fontWeight="700" fontSize="13">Open data</text>
          {['Copernicus (S1, S3)', 'Planetary Computer (S2)', 'Kystverket AIS', 'MET Norway THREDDS', 'ECMWF open data', 'Kartverket, Sodir, Fiskeridir'].map((t, i) => (
            <text key={t} x="85" y={60 + i * 30} textAnchor="middle" fontSize="11">{t}</text>))}
          <rect x="190" y="10" width="380" height="230" rx="12" fill="#fff" stroke="#116a8f" strokeWidth="2" />
          <text x="380" y="32" textAnchor="middle" fontWeight="700" fontSize="13">Laptop: Docker Compose (Linux containers)</text>
          {[['Prefect server', 210, 50], ['PostGIS', 400, 50], ['Pipeline worker', 210, 130], ['ESA SNAP 14', 400, 130]].map(([t, x, y]) => (
            <g key={String(t)}><rect x={Number(x)} y={Number(y)} width="150" height="56" rx="10" fill="#f7fbfd" stroke="#9fb3c3" />
              <text x={Number(x) + 75} y={Number(y) + 33} textAnchor="middle" fontSize="12" fontWeight="600">{t}</text></g>))}
          <text x="380" y="215" textAnchor="middle" fontSize="11" fill="#5a6875">msp CLI · flows · STAC catalogue · results · demo export</text>
          <rect x="600" y="70" width="150" height="110" rx="12" fill="#0b3954" />
          <text x="675" y="105" textAnchor="middle" fill="#fff" fontWeight="700" fontSize="13">This website</text>
          <text x="675" y="125" textAnchor="middle" fill="#cfe0ea" fontSize="11">FastAPI + React</text>
          <text x="675" y="142" textAnchor="middle" fill="#cfe0ea" fontSize="11">Azure App Service</text>
          <text x="675" y="159" textAnchor="middle" fill="#cfe0ea" fontSize="11">free tier</text>
          <path d="M160 125 H190" stroke="#9fb3c3" strokeWidth="2" /><path d="M570 125 H600" stroke="#9fb3c3" strokeWidth="2" />
        </svg>
      </section>
      <section className="card" id="formats" style={{ marginTop: '1rem' }}>
        <h2>Data formats and where each is used</h2>
        <table className="data"><thead><tr><th>Format</th><th>What it is</th><th>Used for</th></tr></thead>
          <tbody>{FORMATS.map(([a, b, c]) => <tr key={a}><td><b>{a}</b></td><td>{b}</td><td>{c}</td></tr>)}</tbody></table>
      </section>
      <div className="grid2" style={{ marginTop: '1rem' }}>
        <section className="card">
          <h2>Tools</h2>
          <table className="data"><tbody>{TOOLS.map(([a, b]) => <tr key={a}><td><b>{a}</b></td><td>{b}</td></tr>)}</tbody></table>
        </section>
        <section className="card" id="docs">
          <h2>Documentation</h2>
          <ul className="small">{DOCS.map(([f, t]) => <li key={f}><a href={`${REPO_URL}/blob/main/docs/${f}`} target="_blank" rel="noreferrer">{t}</a></li>)}</ul>
          <p className="small">Source code, tests, the Docker Compose stack and the data notice are in the <a href={REPO_URL} target="_blank" rel="noreferrer">GitHub repository</a>. The full pipeline runs locally on Windows (<code>run.ps1</code>) or Linux (<code>run.sh</code>).</p>
        </section>
      </div>
      <Tour id="built" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}
