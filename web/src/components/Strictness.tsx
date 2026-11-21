import { useMemo, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { pct } from '../lib/data'

type Bin = { bin: number; predicted: number; observed: number; n: number }

/**
 * "How strict should the AI be?" The AI calls a spot a ship above a cut-off (50% on this site). The
 * test-set calibration bins hold the exact counts of ships and false alarms in each 10% band of AI
 * probability, so the trade-off at every cut-off from 10% to 90% is exact, not an estimate.
 */
export function Strictness({ bins, threshold }: { bins: Bin[]; threshold: number }) {
  const rows = useMemo(() => {
    const per = Array.from({ length: 10 }, (_, b) => {
      const x = bins.find((r) => r.bin === b)
      const n = x?.n ?? 0
      const ships = x ? Math.round(x.n * x.observed) : 0
      return { ships, fas: n - ships }
    })
    const ships = per.reduce((a, r) => a + r.ships, 0)
    const fas = per.reduce((a, r) => a + r.fas, 0)
    return Array.from({ length: 9 }, (_, k) => {
      const t = (k + 1) / 10
      const kept = per.slice(k + 1).reduce((a, r) => a + r.ships, 0)
      const keptFa = per.slice(k + 1).reduce((a, r) => a + r.fas, 0)
      return { t, kept, ships, removed: fas - keptFa, fas, precision: kept + keptFa ? kept / (kept + keptFa) : 1 }
    })
  }, [bins])
  const [t, setT] = useState(threshold)
  const r = rows.reduce((b, x) => (Math.abs(x.t - t) < Math.abs(b.t - t) ? x : b), rows[0])
  const chart = rows.map((x) => ({ t: Math.round(100 * x.t), kept: (100 * x.kept) / x.ships, removed: (100 * x.removed) / x.fas }))
  const advice = r.t <= 0.3
    ? 'Lenient: almost every real ship survives, but many false alarms are left to check by hand. Good when missing a boat is costly, for example when looking for boats that do not report.'
    : r.t >= 0.7
      ? 'Strict: very few false alarms remain, but more real ships are thrown away. Good for a quick, clean picture when a missed small boat does not matter.'
      : 'Balanced: most false alarms go, and most real ships stay. This is the setting used on the rest of the site.'
  return (
    <section className="card" id="strictness" style={{ marginTop: '1rem' }}>
      <h2>How strict should the AI be?</h2>
      <p className="small muted" style={{ marginTop: 0 }}>The AI gives every bright spot a chance of being a ship and keeps the spots above a cut-off. Move the cut-off to see the trade-off on the {r.ships + r.fas} test cases. These are exact counts, not estimates.</p>
      <div className="tool-grid">
        <div>
          <label className="slider-row">Keep a spot as a ship when the AI says at least <b>{pct(r.t)}</b>
            <input type="range" min={0.1} max={0.9} step={0.1} value={r.t} aria-label="AI cut-off" onChange={(e) => setT(+e.target.value)} />
          </label>
          <div className="row" style={{ gap: '1.5rem', marginTop: '0.8rem', flexWrap: 'wrap' }}>
            <div className="stat"><span className="num" style={{ color: 'var(--matched)' }}>{pct(r.kept / r.ships)}</span><span className="lbl">real ships kept ({r.kept} of {r.ships})</span></div>
            <div className="stat"><span className="num" style={{ color: 'var(--ai)' }}>{pct(r.removed / r.fas)}</span><span className="lbl">false alarms removed ({r.removed} of {r.fas})</span></div>
            <div className="stat"><span className="num">{pct(r.precision)}</span><span className="lbl">of the kept spots are real ships</span></div>
          </div>
          <p className="small note-box">{advice}</p>
        </div>
        <ResponsiveContainer width="100%" height={230}>
          <LineChart data={chart} margin={{ left: -14, right: 10, top: 8 }}>
            <CartesianGrid stroke="#eef1f4" />
            <XAxis dataKey="t" unit="%" fontSize={11} label={{ value: 'cut-off', position: 'insideBottomRight', offset: -2, fontSize: 11 }} />
            <YAxis domain={[0, 100]} unit="%" fontSize={11} />
            <Tooltip formatter={(v, n) => [`${Number(v).toFixed(0)}%`, String(n)]} labelFormatter={(l) => `cut-off ${l}%`} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine x={Math.round(100 * r.t)} stroke="#17212b" strokeDasharray="3 3" />
            <Line dataKey="kept" name="real ships kept" stroke="var(--matched)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
            <Line dataKey="removed" name="false alarms removed" stroke="var(--ai)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
