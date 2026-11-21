import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Intro, Legend, LoadState, Tour, useTour, type TourStep } from '../components/ui'
import { ci, niceDay, pct, useJSON, type Pass, type Summary } from '../lib/data'

type Day = { day: string; s2_cloud: number | null; s2_observed: number | null; radar: number; sst_valid: number | null; truecolor: string | null }

const STEPS: TourStep[] = [
  { target: '#cal', title: 'One square per day', text: 'The top strip is the optical satellite: white = cloudy, blue = clear, empty = no image that day. The bottom strip marks each radar pass.' },
  { target: '#cloud-stat', title: 'The blind share', text: 'How many of the optical days had more than half of the sea under cloud.' },
  { target: '#preview', title: 'Look for yourself', text: 'Click a day to see its true-color image, when a clear enough one exists.' },
]

const shade = (c: number) => {
  const v = Math.round(60 + 190 * c)
  return `rgb(${v}, ${Math.round(v + (1 - c) * 20)}, ${Math.round(Math.min(255, v + (1 - c) * 120))})`
}

export default function Clouds() {
  const tour = useTour('clouds')
  const sQ = useJSON<Summary>('summary.json')
  const dQ = useJSON<Day[]>('clouds.json')
  const s = sQ.data
  const days = dQ.data
  const { data: passes } = useJSON<Pass[]>('passes.json')
  const [sel, setSel] = useState<Day | null>(null)
  const [imgReady, setImgReady] = useState(false)
  const go = useNavigate()
  if (!s || !days) return <main className="page"><LoadState qs={[sQ, dQ]} what="calendar" /></main>
  const radarDays = days.filter((d) => d.radar > 0).length
  const sstDays = days.filter((d) => (d.sst_valid ?? 0) > 0.05).length
  // day label: the month is written on the first day shown and on the 1st of each month
  const label = (day: string, k: number) => {
    const t = new Date(day)
    const month = k === 0 || t.getUTCDate() === 1 ? t.toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' }) : null
    return <>{month && <span className="mon">{month}</span>}{t.getUTCDate()}</>
  }
  const passOn = (day: string) => passes?.find((p) => p.time.slice(0, 10) === day)
  const pickDay = (d: Day) => { setSel(d); setImgReady(false) }
  return (
    <main className="page">
      <Intro
        what={<>A day-by-day calendar of the summer study period: when the optical satellite could see the sea, and when the radar passed over. Optical data was collected for summer only; the winter passes are radar only.</>}
        how={<>Scan the two strips. Hover over a day for its numbers. Click an optical day to see its image, or a radar day to open that pass on the sea map.</>}
        notice={<>Most optical days are white with cloud. The radar row has no gaps for weather: it sees through cloud, rain and darkness.</>}
      />
      <div className="grid3" style={{ marginBottom: '1rem' }}>
        <div className="card stat" id="cloud-stat"><span className="num">{pct(s.cloud.share)}</span>
          <span className="lbl">of {s.cloud.days} optical (Sentinel-2) days were blind: more than half the sea under cloud ({ci(s.cloud.lo, s.cloud.hi)})</span></div>
        <div className="card stat"><span className="num" style={{ color: 'var(--radar)' }}>{s.counts.passes}</span>
          <span className="lbl">radar passes on {radarDays} days, every one usable, whatever the weather</span></div>
        <div className="card stat"><span className="num" style={{ color: 'var(--unmatched)' }}>{sstDays}</span>
          <span className="lbl">days with usable sea surface temperature (an infrared sensor, also stopped by cloud)</span></div>
      </div>
      <section className="card" id="cal">
        <div className="row"><h2 style={{ margin: 0 }}>{niceDay(days[0].day)} to {niceDay(days[days.length - 1].day)}</h2><span className="spacer" />
          <Legend items={[
            { color: shade(0), label: 'clear', square: true }, { color: shade(1), label: 'cloudy', square: true },
            { color: '#f1f3f5', label: 'no optical image', square: true }, { color: 'var(--radar)', label: 'radar pass', square: true },
          ]} />
        </div>
        <div className="small muted" style={{ margin: '0.6rem 0 0.2rem' }}>Optical (Sentinel-2): cloud over the sea</div>
        <div className="cal">
          {days.map((d, k) => (
            <button key={d.day} className={`d ${sel?.day === d.day ? 'sel' : ''}`} onClick={() => pickDay(d)} aria-label={niceDay(d.day)}
              data-tip={`${niceDay(d.day)}: ${d.s2_cloud == null ? 'no optical image' : `${pct(d.s2_cloud)} of the sea under cloud`}`}
              style={{ background: d.s2_cloud == null ? '#f1f3f5' : shade(d.s2_cloud), color: d.s2_cloud == null || d.s2_cloud > 0.55 ? '#5a6875' : '#fff', border: 0, cursor: 'pointer' }}>
              {label(d.day, k)}
            </button>
          ))}
        </div>
        <div className="small muted" style={{ margin: '0.6rem 0 0.2rem' }}>Radar (Sentinel-1): passes over the area</div>
        <div className="cal">
          {days.map((d) => {
            const p = d.radar ? passOn(d.day) : undefined
            const tip = `${niceDay(d.day)}: ${d.radar ? `${d.radar} radar pass${d.radar > 1 ? 'es' : ''}${p ? ', click to open it on the sea map' : ''}` : 'no pass in this study'}`
            return p
              ? <button key={d.day} className="d" data-tip={tip} aria-label={`Open the ${niceDay(d.day)} radar pass on the sea map`} onClick={() => go(`/map?pass=${p.id}`)}
                  style={{ background: 'var(--radar)', height: 18, border: 0, cursor: 'pointer' }}>{d.radar > 1 ? '2' : ''}</button>
              : <div key={d.day} className="d" data-tip={tip} style={{ background: d.radar ? 'var(--radar)' : '#f1f3f5', height: 18 }}>{d.radar > 1 ? '2' : ''}</div>
          })}
        </div>
        <p className="tiny muted" style={{ marginTop: '0.6rem' }}>Radar gaps are days without a selected pass, not weather: the study used 24 of the passes over the area to stay within its download budget.</p>
      </section>
      <section className="card" id="preview" style={{ marginTop: '1rem' }}>
        <h2>{sel ? niceDay(sel.day) : 'Click a day'}</h2>
        {!sel ? <p className="small muted">Pick a day in the calendar to see its optical image.</p>
          : sel.truecolor ? <>
            {!imgReady && <p className="small muted">Loading the image…</p>}
            <img key={sel.day} src={`/data/${sel.truecolor}`} alt={`True-color image ${sel.day}`} onLoad={() => setImgReady(true)}
              onError={() => setImgReady(true)}
              style={{ maxWidth: '100%', maxHeight: '70vh', objectFit: 'contain', borderRadius: 8, display: 'block' }} />
            <p className="tiny muted">Sentinel-2 true-color image of the study area, {pct(sel.s2_cloud ?? 0)} of the sea under cloud.</p>
          </>
          : <p className="small">{sel.s2_cloud == null ? 'No optical image that day.' : `Too cloudy to show (${pct(sel.s2_cloud)} of the sea under cloud). The radar does not care.`}</p>}
      </section>
      <Tour id="clouds" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}
