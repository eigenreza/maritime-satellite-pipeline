import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, ErrorBar, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ClassTick } from '../components/charts'
import { ShipTool } from '../components/ShipTool'
import { Intro, LoadState, SeasonSwitch, Term, Tour, useSeason, useTour, type TourStep } from '../components/ui'
import { ci, fmt, MIN_N, oddsFor, pct, rowsFor, seaStateText, statsFor, unmatchedTrend, useJSON, type Interval, type LogitModel, type Odds, type SizeRow, type Summary } from '../lib/data'
import { detectionChance, detectionModel } from '../lib/model'
import { casesLabel } from '../lib/text'

const STEPS: TourStep[] = [
  { target: '#ship-tool', title: 'Try it', text: 'Pick a ship and the weather. The model answers in plain words and shows what drives the answer.' },
  { target: '#by-length', title: 'Ship size', text: 'Each bar is the share of AIS ships the radar found. The thin lines show the 95% uncertainty interval.' },
  { target: '#by-waves', title: 'Sea state', text: 'The same share split by wave height and by wind speed. Read the orange note: these raw bars mix sea state with ship size.' },
  { target: '#why', title: 'Why', text: 'A short explanation of what the radar actually measures.' },
]

type Row = Interval & { cls: string }

/** Why the raw bars can mislead: which ships were out in each sea-state class. */
function SizeMix({ rows, what }: { rows?: SizeRow[]; what: string }) {
  const ok = (rows ?? []).filter((r) => r.n >= MIN_N)
  if (ok.length < 2) return null
  const lo = ok[0]
  const hi = ok[ok.length - 1]
  if (hi.median_length_m <= lo.median_length_m * 1.15) return null
  return (
    <p className="small note-box">
      Careful: these bars mix sea state with ship size. In {lo.cls} {what} the typical AIS ship out at sea was {fmt(lo.median_length_m, 0)} m long,
      in {hi.cls} it was {fmt(hi.median_length_m, 0)} m: fewer small boats were out in rough weather, and big ships are easier to find.
      The ship tool above and the table further down compare ships of the same size.
    </p>
  )
}

/** Model estimate for a sea-state class that has too few ships to measure (typical ship, class midpoint). */
function classFallback(m: LogitModel | null, kind: 'wave' | 'wind', cls: string, winter: number): number | null {
  if (!m) return null
  const nums = (cls.match(/[\d.]+/g) ?? []).map(Number)
  if (!nums.length) return null
  const x = cls.startsWith('over') ? nums[0] + (kind === 'wave' ? 0.5 : 2) : (nums[0] + (nums[1] ?? nums[0])) / 2
  const { length_m: L, wind_ms: W } = m.typical
  return kind === 'wave' ? detectionChance(m, L, W, x, winter) : detectionChance(m, L, x, 0, winter)
}

function RateChart({ rows = [], color, label, unit = 'ships', fallback }: { rows?: Row[]; color: string; label: string; unit?: string; fallback?: (cls: string) => number | null }) {
  const data = rows.map((r) => r.n >= MIN_N
    ? { cls: r.cls, rate: r.rate * 100, err: [(r.rate - r.lo) * 100, (r.hi - r.rate) * 100], n: r.n }
    : { cls: r.cls, rate: null, err: null, n: r.n })
  const nOf = new Map(data.map((d) => [d.cls, d.n]))
  const sub = (v: string) => {
    const n = nOf.get(v) ?? 0
    const f = n >= MIN_N ? null : fallback?.(v)
    return casesLabel(n, unit, n >= MIN_N, f == null ? null : pct(f))
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ left: -14, right: 8, top: 8, bottom: 8 }}>
        <CartesianGrid stroke="#eef1f4" vertical={false} />
        <XAxis dataKey="cls" interval={0} height={36} tick={<ClassTick sub={sub} />} />
        <YAxis domain={[0, 100]} unit="%" fontSize={11} />
        <Tooltip formatter={(v) => [`${Number(v).toFixed(0)}%`, label]} labelFormatter={(l, p) => `${l}: ${p?.[0]?.payload?.n ?? 0} cases`} />
        <Bar dataKey="rate" fill={color} radius={[6, 6, 0, 0]} isAnimationActive={false}>
          <ErrorBar dataKey="err" width={6} stroke="#17212b" strokeWidth={1.2} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function OddsTable({ a = [], b = [] }: { a?: Odds[]; b?: Odds[] }) {
  const terms = [...new Set([...a, ...b].map((r) => r.term))]
  const cell = (r?: Odds) => r ? <><b>{fmt(r.odds_ratio, 2)}</b> <span className="tiny muted">({fmt(r.lo, 2)} to {fmt(r.hi, 2)})</span></> : <span className="muted">not in this model</span>
  return (
    <table className="data">
      <thead><tr><th>Effect on the odds of detection</th><th><Term k="oddsratio">Odds ratio</Term>, no season term</th><th>With a season term</th></tr></thead>
      <tbody>
        {terms.map((t) => (
          <tr key={t}><td>{t}</td><td>{cell(a.find((r) => r.term === t))}</td><td>{cell(b.find((r) => r.term === t))}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

export default function Radar() {
  const tour = useTour('radar')
  const sQ = useJSON<Summary>('summary.json')
  const s = sQ.data
  const loc = useLocation()
  const [season, setSeason] = useSeason('both')
  // links to /radar#ship-tool land on the tool once the results have loaded
  useEffect(() => {
    if (s && loc.hash) document.querySelector(loc.hash)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [s, loc.hash])
  if (!s) return <main className="page"><LoadState qs={[sQ]} what="results" /></main>
  const m = detectionModel(s)
  const winter = season === 'winter' ? 1 : 0
  const st = statsFor(s, season)
  const odds = oddsFor(s, season)
  const nPasses = season === 'winter' ? s.counts.winter_passes : season === 'summer' ? s.counts.eval_passes : s.counts.eval_passes + s.counts.winter_passes
  const rough = (rowsFor(s.by_wave, season) ?? []).filter((r) => /^(3|4|over)/.test(r.cls)).reduce((a, r) => a + r.n, 0)
  const um = st?.unmatched_overall
  return (
    <main className="page">
      <Intro
        what={<>How often the radar found the ships that reported their position by AIS, split by ship size, wind and waves, over {nPasses} test passes ({season === 'both' ? 'summer and winter' : season}).</>}
        how={<>Try the ship tool first, then read the bar charts (hover for exact numbers). The thin black lines are 95% intervals.</>}
        notice={<>Size decides most of it: most big ships are found, few small boats. {seaStateText(odds, rough)}</>}
      />
      <div className="season-bar"><SeasonSwitch value={season} onChange={setSeason} /><span className="tiny muted">Summer: the {s.counts.eval_passes} summer evaluation passes. Winter: the {s.counts.winter_passes} winter passes, never used to tune anything.</span></div>
      <ShipTool s={s} season={season} />
      <div className="grid2" style={{ marginTop: '1rem' }}>
        <section className="card" id="by-length">
          <h2>By ship length</h2>
          <RateChart rows={rowsFor(s.by_length, season)} color="var(--ais)" label="found by radar" />
        </section>
        <section className="card" id="by-waves">
          <h2>By wave height</h2>
          <RateChart rows={rowsFor(s.by_wave, season)} color="var(--radar)" label="found by radar" fallback={(c) => classFallback(m, 'wave', c, winter)} />
          <SizeMix rows={rowsFor(s.size_by_wave, season)} what="waves" />
        </section>
        <section className="card">
          <h2>By wind speed</h2>
          <RateChart rows={rowsFor(s.by_wind, season)} color="var(--matched)" label="found by radar" fallback={(c) => classFallback(m, 'wind', c, winter)} />
          <SizeMix rows={rowsFor(s.size_by_wind, season)} what="wind" />
        </section>
        <section className="card">
          <h2>Detections without an AIS match</h2>
          {um && <p className="small">Overall, <b>{pct(um.rate)}</b> of the radar detections at sea ({ci(um.lo, um.hi)}) had no AIS ship within reach. By wave height:</p>}
          <RateChart rows={rowsFor(s.unmatched_by_wave, season)} color="var(--unmatched)" label="no AIS match" unit="detections" />
          <p className="small muted">Likely explanations: boats that do not carry AIS (open AIS data also hides fishing vessels under 15 m and leisure craft under 45 m), fixed objects not in the mask, and bright patches of rough sea. {{
            up: 'The share grows with the waves, which points at the sea itself.',
            down: 'The share is highest in calm seas, when small boats without AIS are most likely out; rough seas did not add false alarms in these passes.',
            flat: 'The share changes little with the waves.',
          }[unmatchedTrend(rowsFor(s.unmatched_by_wave, season) ?? [])]}</p>
        </section>
      </div>
      <p className="tiny muted" style={{ margin: '0.6rem 0 0' }}>Where a class has fewer than {MIN_N} cases, no bar is drawn. The label gives the model's estimate for a typical ship instead: the general rule, not a measurement.</p>

      <div className="grid2" style={{ marginTop: '1rem' }}>
        <section className="card">
          <h2>All three effects together</h2>
          <OddsTable a={s.logistic.without_season} b={s.logistic.with_season} />
          <p className="tiny muted">Odds ratio above 1: more likely to be found; below 1: less likely. Both models are fitted on the {(s.logistic.with_season ?? s.logistic.without_season ?? [])[0]?.n} AIS ship positions of the summer evaluation and winter passes; the second adds a winter term, so the other effects are compared within a season. Intervals from resampling whole passes within each season.</p>
        </section>
        <section className="card" id="why">
          <h2>Why this happens</h2>
          <p className="small">A radar satellite measures how much of its own microwave pulse comes back. Calm water reflects the pulse away like a mirror and looks dark. A ship's steel hull and superstructure send it straight back and look bright.</p>
          <p className="small">Wind roughens the surface and waves tilt it toward the satellite, so the background gets brighter. The detector compares each pixel with the sea around it; when the sea itself is bright, a small boat no longer stands out.</p>
          <p className="small muted">SAR, synthetic aperture radar: a satellite radar that sees through clouds and at night. Sentinel-1 images this coast several times a week with 10 m pixels.</p>
        </section>
      </div>
      <Tour id="radar" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}
