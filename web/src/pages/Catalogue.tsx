import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Intro, LoadState, Tour, useTour, type TourStep } from '../components/ui'
import { getJSON, niceTime, useJSON } from '../lib/data'

type Item = { id: string; collection: string; datetime: string | null; static?: boolean; bbox: number[]; preview: string | null; stac: string; summary: Record<string, string | number | null> }
type Cat = { collections: { id: string; title: string; description: string; count: number }[]; items: Item[] }

const AREAS: Record<string, { label: string; bbox: number[] }> = {
  all: { label: 'Whole study area', bbox: [4.0, 60.0, 6.0, 61.3] },
  fedje: { label: 'Fedje, Mongstad and Sture', bbox: [4.6, 60.55, 5.2, 60.95] },
  bergen: { label: 'Bergen and its approaches', bbox: [4.9, 60.2, 5.45, 60.5] },
  west: { label: 'Open sea to the west', bbox: [4.0, 60.0, 4.6, 61.3] },
}

const STEPS: TourStep[] = [
  { target: '#filters', title: 'Search', text: 'Filter by product, area and acquisition date, or type part of an item name.' },
  { target: '#results', title: 'Results', text: 'Every derived product the pipeline wrote, described in STAC. Click one to see its metadata and preview.' },
  { target: '#item-view', title: 'Item details', text: 'The STAC metadata record: where, when, which files, which processing.' },
]

const overlaps = (a: number[], b: number[]) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]

export default function Catalogue() {
  const tour = useTour('catalogue')
  const catQ = useJSON<Cat>('catalogue.json')
  const data = catQ.data
  const [col, setCol] = useState('all')
  const [area, setArea] = useState('all')
  const days = useMemo(() => (data?.items ?? []).flatMap((it) => (it.datetime ? [it.datetime.slice(0, 10)] : [])).sort(), [data])
  const [fromSet, setFrom] = useState<string | null>(null)
  const [toSet, setTo] = useState<string | null>(null)
  const from = fromSet ?? days[0] ?? ''
  const to = toSet ?? days[days.length - 1] ?? ''
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<Item | null>(null)
  const [json, setJson] = useState<unknown>(null)
  const items = useMemo(() => (data?.items ?? []).filter((it) =>
    (col === 'all' || it.collection === col) && overlaps(it.bbox, AREAS[area].bbox)
    && (it.static || !it.datetime || (it.datetime.slice(0, 10) >= from && it.datetime.slice(0, 10) <= to)) && it.id.toLowerCase().includes(q.toLowerCase())), [data, col, area, from, to, q])
  if (!data) return <main className="page"><LoadState qs={[catQ]} what="catalogue" /></main>
  const open = async (it: Item) => {
    setSel(it)
    setJson(null)
    try { setJson(await getJSON(it.stac)) } catch { setJson({ error: 'Could not load this record. Click the item again to retry.' }) }
  }
  return (
    <main className="page">
      <Intro
        what={<>A searchable catalogue of every product the pipeline made: radar images, ship detections, cloud masks, sea temperature, wind and waves.</>}
        how={<>Choose a product type, an area and dates, then click an item to see its metadata and a preview.</>}
        notice={<>Every item carries the same core fields (where, when, which files) plus extensions for radar, projection and processing history. That is what makes data findable.</>}
      />
      <section className="card" style={{ marginBottom: '1rem' }}>
        <p className="small" style={{ margin: 0 }}><b>STAC</b> (SpatioTemporal Asset Catalog) is an open standard for describing satellite and map data as small JSON records. A program can search any STAC catalogue by place, time and product the same way, without knowing how the files are stored.</p>
      </section>
      <div className="grid2">
        <section className="card">
          <div id="filters" className="stack" style={{ gap: '0.5rem' }}>
            <label className="small" data-tip="Product type (STAC collection)">Product
              <select value={col} onChange={(e) => setCol(e.target.value)} style={{ width: '100%', padding: '0.4rem' }}>
                <option value="all">All products ({data.items.length})</option>
                {data.collections.map((c) => <option key={c.id} value={c.id}>{c.title} ({c.count})</option>)}
              </select></label>
            <label className="small" data-tip="Only items that cover this part of the study area">Area
              <select value={area} onChange={(e) => setArea(e.target.value)} style={{ width: '100%', padding: '0.4rem' }}>
                {Object.entries(AREAS).map(([k, a]) => <option key={k} value={k}>{a.label}</option>)}
              </select></label>
            <div className="row">
              <label className="small" data-tip="Earliest acquisition date">From <input type="date" value={from} min={days[0]} max={days[days.length - 1]} onChange={(e) => setFrom(e.target.value)} /></label>
              <label className="small" data-tip="Latest acquisition date">To <input type="date" value={to} min={days[0]} max={days[days.length - 1]} onChange={(e) => setTo(e.target.value)} /></label>
            </div>
            <input placeholder="Search item names…" value={q} onChange={(e) => setQ(e.target.value)} style={{ padding: '0.45rem' }} data-tip="Type part of an item name, for example S1C or 0912" />
          </div>
          <div id="results" style={{ marginTop: '0.75rem', maxHeight: 460, overflow: 'auto' }}>
            <div className="small muted">{items.length} items</div>
            <table className="data small">
              <tbody>
                {items.slice(0, 400).map((it) => (
                  <tr key={it.id} onClick={() => open(it)} style={{ cursor: 'pointer', background: sel?.id === it.id ? '#eef6fb' : undefined }}>
                    <td><b>{it.id}</b><br /><span className="muted">{data.collections.find((c) => c.id === it.collection)?.title}</span></td>
                    <td style={{ whiteSpace: 'nowrap' }}>{it.static || !it.datetime ? <span className="muted" data-tip="A reference layer valid for the whole study: no single time">static layer</span> : niceTime(it.datetime)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="card" id="item-view">
          {!sel ? <p className="muted small">Click an item on the left to see its metadata.</p> : (
            <div>
              <h2 style={{ wordBreak: 'break-all' }}>{sel.id}</h2>
              {/^S1[A-D]_\d{8}T\d{6}_R\d{3}/.test(sel.id) && (
                <p className="small"><Link to={`/map?pass=${sel.id.match(/^S1[A-D]_\d{8}T\d{6}_R\d{3}/)![0]}`}>Open this radar pass on the sea map →</Link></p>
              )}
              {sel.preview && <img src={`/data/${sel.preview}`} alt="Preview" style={{ maxWidth: '100%', maxHeight: 260, borderRadius: 8, background: '#0e2a3d' }} />}
              <table className="data small">
                <tbody>{Object.entries(sel.summary).map(([k, v]) => <tr key={k}><td className="muted">{k}</td><td>{String(v ?? 'n/a')}</td></tr>)}</tbody>
              </table>
              <details style={{ marginTop: '0.5rem' }}>
                <summary className="small" data-tip="The full STAC item record as JSON">Full STAC record</summary>
                <pre style={{ fontSize: 11, maxHeight: 300, overflow: 'auto', background: '#f6f8fa', padding: 8, borderRadius: 8 }}>{json ? JSON.stringify(json, null, 1) : 'Loading…'}</pre>
              </details>
            </div>
          )}
        </section>
      </div>
      <Tour id="catalogue" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}
