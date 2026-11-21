import { useEffect, useState } from 'react'
import { Intro, Legend, LoadState, Tour, useTour, type TourStep } from '../components/ui'
import { fmt, niceTime, useJSON } from '../lib/data'
import { count } from '../lib/text'

type Row = { replay_time: string; flow: string; task: string; status: string; attempt: number; message: string }
type Fresh = { source: string; window: 'summer' | 'winter'; label: string; limit_hours: number; why: string; checks: number
  fresh: number; stale: number; missing: number; timeline: [string, string][] }
type Episode = { source: string; label: string; status: string; raised: string; cleared: string | null }
type Delivery = { source: string; season: string; count: number; published_median_h: number | null; published_max_h: number | null
  product_median_h: number; product_max_h: number; total_median_h: number; total_max_h: number }
type Ops = {
  flows: { flow: string; label: string; runs: number; completed: number; retried: number; failed: number; recovered: number; seconds: number; records: number; gigabytes: number }[]
  freshness: Fresh[]
  alerts: { replay_time: string; source: string; message: string }[]
  alert_count: number
  episodes: Episode[]
  incidents: { kind: 'timeout' | 'corrupt' | 'outage'; title: string; rows: Row[] }[]
  delivery: Delivery[]
  late?: { time: string; platform: string; season: string; published_h: number }[]
  windows: { summer: [string, string]; winter: [string, string] }
  qc: { product_type: string; products: number; checks: number; failed_checks: number; failures: string[] }[]
  facts: Record<string, number>
}

const INCIDENT_TEXT: Record<string, string> = {
  timeout: 'A forecast download was made to time out (a switch in the configuration). Prefect retried the task and the second attempt succeeded.',
  corrupt: 'A downloaded Sentinel-3 archive was damaged on purpose. The quality check found the broken file, moved it to quarantine, and the flow downloaded it again.',
  outage: 'AIS was switched off for a stretch of the replay. The freshness check raised a stale-data alert; when AIS came back, the missed passes were matched and the alert cleared.',
}

const STEPS: TourStep[] = [
  { target: '#flow-diagram', title: 'The flows', text: 'Each box is a Prefect flow: data comes in on the left, products and the catalogue come out on the right. Dots show data moving.' },
  { target: '#runs', title: 'Run history', text: 'Every task run while replaying both study periods day by day: completed, retried, failed and recovered.' },
  { target: '#freshness', title: 'Freshness', text: 'Is each source up to date on the replay clock? Red means stale: an alert was raised.' },
  { target: '#delivery', title: 'How fast data becomes a product', text: 'From the satellite pass to the provider publishing it, and from there to the finished detection product.' },
  { target: '#incidents', title: 'Three incidents', text: 'A time-out, a damaged file and a source outage, each switched on in the configuration, and what the pipeline did.' },
]

const NODES = [
  { id: 's1_ingest', x: 20, y: 20, t: 'Sentinel-1 radar', s: 'SNAP / Python' },
  { id: 'ais_ingest', x: 20, y: 90, t: 'AIS positions', s: 'Kystverket' },
  { id: 'metocean_ingest', x: 20, y: 160, t: 'Wind and waves', s: 'MET Norway NetCDF' },
  { id: 'ecmwf_ingest', x: 20, y: 230, t: 'Forecasts', s: 'ECMWF GRIB2' },
  { id: 's2_ingest', x: 20, y: 300, t: 'Sentinel-2 optical', s: 'COG windows' },
  { id: 's3_ingest', x: 20, y: 370, t: 'Sentinel-3 SST', s: 'NetCDF' },
  { id: 'ship_detect', x: 250, y: 20, t: 'Ship detection', s: 'CFAR + land mask' },
  { id: 'match', x: 250, y: 120, t: 'Matching', s: 'PostGIS SQL' },
  { id: 'ship_classify', x: 250, y: 220, t: 'AI classifier', s: 'CNN' },
  { id: 'catalogue', x: 480, y: 120, t: 'STAC catalogue', s: 'validated' },
  { id: 'ops', x: 480, y: 220, t: 'Operations', s: 'freshness, alerts' },
  { id: 'demo_export', x: 480, y: 320, t: 'Demo export', s: 'this website' },
]
const EDGES = [['s1_ingest', 'ship_detect'], ['ship_detect', 'match'], ['ais_ingest', 'match'], ['metocean_ingest', 'match'], ['match', 'ship_classify'],
  ['ship_classify', 'catalogue'], ['s2_ingest', 'catalogue'], ['s3_ingest', 'catalogue'], ['ecmwf_ingest', 'ops'], ['catalogue', 'demo_export'], ['ops', 'demo_export'], ['ship_detect', 'catalogue']]

function FlowDiagram() {
  const [t, setT] = useState(0)
  useEffect(() => { const h = setInterval(() => setT((v) => (v + 1) % 100), 60); return () => clearInterval(h) }, [])
  const pos = Object.fromEntries(NODES.map((n) => [n.id, n]))
  return (
    <svg viewBox="0 0 660 420" width="100%" className="flow-svg" role="img" aria-label="Pipeline flow diagram">
      {EDGES.map(([a, b], k) => {
        const p = pos[a], q = pos[b]
        const x1 = p.x + 160, y1 = p.y + 25, x2 = q.x, y2 = q.y + 25
        const f = ((t + k * 13) % 100) / 100
        return (
          <g key={k}>
            <path className="edge" d={`M${x1} ${y1} C ${x1 + 40} ${y1}, ${x2 - 40} ${y2}, ${x2} ${y2}`} />
            <circle className="packet" r={4} cx={x1 + (x2 - x1) * f} cy={y1 + (y2 - y1) * (3 * f * f - 2 * f * f * f)} />
          </g>
        )
      })}
      {NODES.map((n) => (
        <g key={n.id} className="node">
          <rect x={n.x} y={n.y} width={160} height={50} />
          <text x={n.x + 80} y={n.y + 22} textAnchor="middle" fontWeight={700}>{n.t}</text>
          <text x={n.x + 80} y={n.y + 39} textAnchor="middle" fill="#5a6875" fontSize={11}>{n.s}</text>
        </g>
      ))}
    </svg>
  )
}

const COLORS: Record<string, string> = { fresh: '#2b8a3e', stale: '#c92a2a', missing: '#adb5bd' }

export default function OpsPage() {
  const tour = useTour('ops')
  const oQ = useJSON<Ops>('ops.json')
  const o = oQ.data
  if (!o) return <main className="page"><LoadState qs={[oQ]} what="run history" /></main>
  return (
    <main className="page">
      <Intro
        what={<>The data pipeline behind this site: Prefect flows that ingest, process, check and catalogue each source, replayed over both study periods (winter and summer) as if running live.</>}
        how={<>Watch the flow diagram, then read the run history, freshness and quality tables. Hover over any status for details.</>}
        notice={<>Failures happen and are handled. {o.incidents.length ? `${o.incidents.length === 3 ? 'Three' : o.incidents.length} incidents were switched on in the replay: ${o.incidents.map((i) => i.title.toLowerCase()).join('; ')}.` : ''}</>}
      />
      <div className="grid2">
        <section className="card" id="flow-diagram"><h2>Flows</h2><FlowDiagram /></section>
        <section className="card">
          <h2>Pipeline facts</h2>
          <div className="grid2">
            {Object.entries({
              'satellite passes processed': o.facts.passes, 'task runs': o.facts.task_runs, 'retries': o.facts.retries,
              'failures recovered': o.facts.recovered, 'GB of unique raw input data': o.facts.raw_gigabytes, 'STAC items catalogued': o.facts.stac_items,
              'quality checks run': o.facts.qc_checks, 'alerts raised': o.episodes.length,
            }).map(([k, v]) => <div key={k} className="stat"><span className="num">{typeof v === 'number' && !Number.isInteger(v) ? fmt(v, 1) : v}</span><span className="lbl">{k}</span></div>)}
          </div>
          <p className="tiny muted" style={{ marginTop: '0.6rem' }}>Times shown on this page are on the replay clock: each study period replayed day by day. The run counts also include maintenance re-runs outside the replay, such as detecting every pass again after the land mask was corrected.</p>
        </section>
      </div>

      <section className="card" id="runs" style={{ marginTop: '1rem' }}>
        <h2>Run history</h2>
        <table className="data">
          <thead><tr><th>Flow</th><th>Task runs</th><th>Completed</th><th>Retried</th><th>Recovered</th><th>Failed</th><th>Run time</th><th>Records</th></tr></thead>
          <tbody>
            {o.flows.map((f) => (
              <tr key={f.flow}><td data-tip={f.flow}><b>{f.label}</b></td><td>{f.runs}</td><td><span className="pill ok">{f.completed}</span></td>
                <td>{f.retried ? <span className="pill warn">{f.retried}</span> : 0}</td><td>{f.recovered ? <span className="pill info">{f.recovered}</span> : 0}</td>
                <td>{f.failed ? <span className="pill bad">{f.failed}</span> : 0}</td><td>{fmt(f.seconds / 60, 1)} min</td><td>{f.records.toLocaleString('en-US')}</td></tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card" id="delivery" style={{ marginTop: '1rem' }}>
        <h2>From satellite to product</h2>
        <p className="small" style={{ marginTop: 0 }}>For each radar pass: hours from the end of the acquisition to the moment the Copernicus Data Space published it, then from publication to the finished detection product (the next 6-hour slot of the replay clock plus the measured processing time). AIS: from the moment its radar pass reaches the pipeline to stored, matched positions (AIS is fetched for each pass). Forecasts: from the published run to stored fields.</p>
        <table className="data">
          <thead><tr><th>Source</th><th>Season</th><th>Items</th><th>To published (median, slowest)</th><th>To product (median, slowest)</th><th>Total (median, slowest)</th></tr></thead>
          <tbody>
            {o.delivery.map((d) => (
              <tr key={`${d.source}${d.season}`}><td><b>{d.source}</b></td><td>{d.season}</td><td>{d.count}</td>
                <td>{d.published_median_h == null ? <span className="muted">not in the metadata</span> : <>{hours(d.published_median_h)}, {hours(d.published_max_h)}</>}</td>
                <td>{hours(d.product_median_h)}, {hours(d.product_max_h)}</td><td><b>{hours(d.total_median_h)}</b>, {hours(d.total_max_h)}</td></tr>
            ))}
          </tbody>
        </table>
        <p className="tiny muted">Most of the time to product is waiting for the next slot: the replay runs every 6 hours, like a scheduled deployment. Processing itself takes minutes.{(o.late ?? []).map((l) => ` The ${l.platform} scene of ${niceTime(l.time)} was published ${hours(l.published_h)} after acquisition, according to the provider's catalogue; the slowest ${l.season} values come from it.`).join('')}</p>
      </section>

      <div className="grid2" style={{ marginTop: '1rem' }}>
        <section className="card" id="freshness">
          <h2>Freshness per source</h2>
          <Legend items={[{ color: COLORS.fresh, label: 'fresh', square: true }, { color: COLORS.stale, label: 'stale (alert)', square: true }, { color: COLORS.missing, label: 'no data yet', square: true }]} />
          {[...new Set(o.freshness.map((f) => f.source))].map((src) => {
            const rows = o.freshness.filter((f) => f.source === src).sort((a, b) => (a.window === 'winter' ? -1 : 1) - (b.window === 'winter' ? -1 : 1))
            const f0 = rows[0]
            return (
              <div key={src} style={{ marginTop: '0.7rem' }}>
                <div className="small row"><b>{f0.label}</b><span className="muted">must be newer than {f0.limit_hours} h</span></div>
                <div className="tiny muted">{f0.why}</div>
                {rows.map((f) => (
                  <div key={f.window} className="row" style={{ gap: '0.4rem', marginTop: '0.2rem' }}>
                    <span className="tiny muted" style={{ width: 46 }}>{f.window}</span>
                    <div style={{ display: 'flex', height: 12, borderRadius: 4, overflow: 'hidden', flex: 1 }}>
                      {f.timeline.map(([t, st]) => <div key={t} style={{ flex: 1, background: COLORS[st] }} data-tip={`${niceTime(t)}: ${st}`} />)}
                    </div>
                    <span className="pill ok" data-tip="fresh checks">{f.fresh}</span>{f.stale > 0 && <span className="pill bad" data-tip="stale checks (alert)">{f.stale}</span>}
                    {f.missing > 0 && <span className="pill" data-tip="checks before the first data arrived (alert)">{f.missing}</span>}
                  </div>
                ))}
              </div>
            )
          })}
        </section>
        <section className="card">
          <h2>Data quality checks</h2>
          <table className="data">
            <thead><tr><th>Product</th><th>Items</th><th>Checks</th><th>Failed</th></tr></thead>
            <tbody>
              {o.qc.map((q) => (
                <tr key={q.product_type} data-tip={q.failures.length ? `Failed: ${q.failures.join('; ')}` : 'All checks passed'}>
                  <td>{q.product_type}</td><td>{q.products}</td><td>{q.checks}</td>
                  <td>{q.failed_checks ? <span className="pill warn">{q.failed_checks}</span> : <span className="pill ok">0</span>}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="tiny muted">Checks per product: CRS and bounds, nodata share, value ranges, acquisition time, valid COG or NetCDF, CF conventions, archive integrity, valid STAC item. A failed Sentinel-3 check is either a day fully under cloud (no sea seen, so no valid temperature) or the damaged archive of the incident below, caught before use.</p>
        </section>
      </div>

      <section className="card" id="incidents" style={{ marginTop: '1rem' }}>
        <h2>Three incidents, and what the pipeline did</h2>
        <p className="small muted" style={{ marginTop: 0 }}>Each one is switched on in <code>config/pipeline.yaml</code> (<code>ops.incidents</code>), not by editing data. The runbook in <code>docs/operations.md</code> says what a person would check.</p>
        <div className="grid3">
          {o.incidents.map((inc) => (
            <div key={inc.kind}>
              <h3 style={{ margin: '0.3rem 0' }}>{inc.title}</h3>
              <p className="small">{INCIDENT_TEXT[inc.kind]}</p>
              <table className="data small">
                <thead><tr><th>Replay clock</th><th>Step</th><th>Status</th></tr></thead>
                <tbody>{inc.rows.slice(0, 6).map((r, k) => (
                  <tr key={k} data-tip={r.message}><td style={{ whiteSpace: 'nowrap' }}>{niceTime(r.replay_time)}</td><td>{r.flow}{r.attempt > 1 ? ` (try ${r.attempt})` : ''}</td>
                    <td><span className={`pill ${PILL[r.status] ?? ''}`}>{r.status}</span></td></tr>
                ))}</tbody>
              </table>
              {inc.rows.length > 6 && <p className="tiny muted">and {inc.rows.length - 6} more steps in the run log</p>}
            </div>
          ))}
        </div>
      </section>

      <section className="card" style={{ marginTop: '1rem' }} id="alerts">
        <h2>Alerts and when they cleared</h2>
        <table className="data small">
          <thead><tr><th>Source</th><th>Raised</th><th>Cleared</th><th>Kind</th></tr></thead>
          <tbody>{o.episodes.map((e, k) => (
            <tr key={k}><td>{e.label}</td><td style={{ whiteSpace: 'nowrap' }}>{niceTime(e.raised)}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{e.cleared ? <>{niceTime(e.cleared)} <span className="muted">after {hours((Date.parse(e.cleared) - Date.parse(e.raised)) / 3600e3)}</span></> : <span className="pill bad">open at the end of the replay</span>}</td>
              <td>{e.status === 'missing' ? 'no data yet' : 'stale data'}</td></tr>
          ))}</tbody>
        </table>
        <AlertNote o={o} />
      </section>
      <Tour id="ops" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}

const PILL: Record<string, string> = { retried: 'warn', recovered: 'info', completed: 'ok', failed: 'bad', alert: 'bad' }
const hours = (h: number | null | undefined) => (h == null ? 'n/a' : h < 1 / 60 ? 'under 1 min' : h < 1 ? `${Math.round(h * 60)} min` : `${fmt(h, 1)} h`)

/** Plain reading of the alerts: start-up alerts (no data yet) are expected; stale-data alerts are the real ones. */
function AlertNote({ o }: { o: Ops }) {
  const missing = o.episodes.filter((e) => e.status === 'missing').length
  const stale = o.episodes.filter((e) => e.status !== 'missing')
  const open = o.episodes.filter((e) => !e.cleared).length
  return (
    <p className="tiny muted">
      {missing ? `${count(missing, 'alert')} came at the start of a replay window, before the source had delivered its first data: the expected start-up state. ` : ''}
      {`An alert repeats its warning at every 6-hour check until it clears: ${count(o.alert_count, 'warning')} in all. `}
      {stale.length ? `${count(stale.length, 'stale-data alert')} after start-up${open ? `; ${open} still open when the replay ended` : stale.length === 1 ? '; it cleared' : ', all cleared'}.` : 'No source went stale after start-up.'}
    </p>
  )
}
