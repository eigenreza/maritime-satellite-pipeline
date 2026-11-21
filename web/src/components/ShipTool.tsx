import { useMemo, useState } from 'react'
import { ci, MIN_N, pct, rowsFor, type Season, type Summary } from '../lib/data'
import { clampWeather, detectionChance, detectionInterval, lengthClass, shipPresets, unclear, weatherPresets } from '../lib/model'
import { plural, tenRange } from '../lib/text'


// the length slider is logarithmic: small boats get as much room as big ships
const LMIN = 5, LMAX = 400
const toLen = (v: number) => LMIN * Math.pow(LMAX / LMIN, v / 100)
const toSlider = (len: number) => (100 * Math.log(len / LMIN)) / Math.log(LMAX / LMIN)
const roundLen = (l: number) => (l < 100 ? Math.round(l) : Math.round(l / 5) * 5)

function inTen(p: number): string {
  if (p < 0.05) return 'fewer than 1 in 20'
  if (p > 0.95) return 'more than 19 in 20'
  return `about ${Math.round(10 * p)} in 10`
}
const points = (d: number) => {
  const n = Math.abs(Math.round(100 * d))
  return n === 0 ? 'no change' : `${d >= 0 ? '+' : '−'}${n} point${n === 1 ? '' : 's'}`
}

/**
 * "Would radar see this ship?" Describe a ship and the weather; the published detection model answers
 * in plain words with its 95% range, inside the range of weather and ship sizes the study measured.
 */
export function ShipTool({ s, season }: { s: Summary; season: Season }) {
  const range = s.tool_range
  const pooled = season === 'both' || !s.models.with_season
  const m = pooled ? (s.models.without_season ?? s.models.with_season) : s.models.with_season
  const odds = (pooled ? s.logistic.without_season : s.logistic.with_season) ?? s.logistic.with_season ?? []
  const winter = season === 'winter' ? 1 : 0
  // weather presets only inside what the data covers; "Gale" only when enough ships met it
  const seas = useMemo(() => weatherPresets(range, MIN_N), [range])
  const ships = useMemo(() => shipPresets(range), [range])
  const [len, setLen] = useState(30)
  const [wind, setWind] = useState(4)
  const [waves, setWaves] = useState(0.5)
  if (!m) return null
  const { wind: w, waves: h } = clampWeather(range, wind, waves)
  const outside = len < range.length_min_m || len > range.length_max_m
  const p = detectionChance(m, len, w, h, winter)
  const [lo, hi] = detectionInterval(m, len, w, h, winter)
  const typLen = m.typical.length_m, typWind = m.typical.wind_ms
  const base = detectionChance(m, typLen, typWind, 0, winter)
  const term = (k: string) => odds.find((r) => r.term.startsWith(k))
  const drivers = [
    { k: 'Ship size', d: detectionChance(m, len, typWind, 0, winter) - base, unclear: unclear(term('length')), note: `${roundLen(len)} m against a typical ${Math.round(typLen)} m ship` },
    { k: 'Wind', d: detectionChance(m, typLen, w, 0, winter) - base, unclear: unclear(term('wind')), note: `${w} m/s against a typical ${typWind} m/s` },
    { k: 'Waves', d: detectionChance(m, typLen, typWind, h, winter) - base, unclear: unclear(term('wave')), note: `${h.toFixed(1)} m against flat calm` },
  ].sort((a, b) => Math.abs(b.d) - Math.abs(a.d))
  const cls = lengthClass(len)
  const measured = rowsFor(s.by_length, season)?.find((r) => r.cls === cls)
  // the regular schedule: the summer selection covers every pass of the chosen orbits (winter is a sea-state sample)
  const perWeek = s.counts.passes_summer / Math.max(s.periods.summer.weeks, 1)
  const perWeekN = Math.round(perWeek)
  const week = 1 - Math.pow(1 - p, perWeek)
  const seasonWords = season === 'both' ? 'both seasons together' : season === 'winter' ? 'winter' : 'summer'
  return (
    <section className="card ship-tool" id="ship-tool">
      <h2>Would radar see this ship?</h2>
      <p className="small muted" style={{ marginTop: 0 }}>Pick a ship and the weather, or set your own. The answer comes from the detection model fitted to the summer evaluation and winter passes ({seasonWords} shown), and stays inside the weather this study measured.</p>
      <div className="tool-grid">
        <div>
          <div className="chips" role="group" aria-label="Ship presets">
            {ships.map((x) => (
              <button key={x.label} className={`chip ${Math.abs(len - x.len) < 0.5 ? 'on' : ''}`} onClick={() => setLen(x.len)}>{x.label} · {x.len} m</button>
            ))}
          </div>
          <label className="slider-row">Ship length <b>{roundLen(len)} m</b>
            <input type="range" min={0} max={100} step={0.5} value={toSlider(len)} aria-label="Ship length"
              onChange={(e) => setLen(toLen(+e.target.value))} />
          </label>
          <div className="chips" role="group" aria-label="Weather presets" style={{ marginTop: '0.6rem' }}>
            {seas.map((x) => (
              <button key={x.label} className={`chip ${w === x.wind && h === x.waves ? 'on' : ''}`} onClick={() => { setWind(x.wind); setWaves(x.waves) }}>{x.label}</button>
            ))}
          </div>
          <label className="slider-row">Wind <b>{w} m/s</b>
            <input type="range" min={0} max={range.wind_max_ms} step={0.5} value={w} aria-label="Wind speed" onChange={(e) => setWind(+e.target.value)} />
          </label>
          <label className="slider-row">Waves <b>{h.toFixed(1)} m</b>
            <input type="range" min={0} max={range.wave_max_m} step={0.1} value={h} aria-label="Wave height" onChange={(e) => setWaves(+e.target.value)} />
          </label>
          <p className="tiny muted" style={{ marginTop: '0.4rem' }}>The sliders end at {range.wind_max_ms} m/s and {range.wave_max_m} m: 97.5% of the AIS ships in the data met calmer weather than that.</p>
        </div>
        <div className="tool-answer" aria-live="polite">
          {outside ? (
            <>
              <div className="big">Outside what this study measured</div>
              <p className="small" style={{ margin: '0.5rem 0' }}>Ships of {roundLen(len)} m are outside the lengths that most AIS ships in the data had ({range.length_min_m} to {range.length_max_m} m), so the model would be guessing. Pick a length in that range.</p>
            </>
          ) : (
            <>
              <div className="big">{inTen(p)}</div>
              <div className="small muted">95% range: {tenRange(lo, hi)}</div>
              <div className="meter" aria-hidden="true"><div className="meter-range" style={{ left: `${100 * lo}%`, width: `${100 * Math.max(0, hi - lo)}%` }} /><div style={{ width: `${100 * p}%` }} /></div>
              <p style={{ margin: '0.5rem 0' }}>
                A {roundLen(len)} m ship in {w} m/s of wind and {h.toFixed(1)} m waves ({seasonWords}): in a single Sentinel-1 pass, radar finds <b>{pct(p)}</b> of ships like this ({ci(lo, hi)}).
              </p>
              <p className="small" style={{ margin: '0.4rem 0' }}>
                With about {perWeekN} {plural(perWeekN, 'selected pass', 'selected passes')} a week (the summer schedule), a ship like this that stays at sea for a week would show up at least once with a chance of about <b>{pct(week)}</b> (if every pass were an independent try).
              </p>
              <div className="small" style={{ marginTop: '0.6rem' }}><b>What drives it</b> (each factor alone, against a typical ship in calm water):</div>
              <ul className="drivers">
                {drivers.map((x) => (
                  <li key={x.k}><span>{x.k}</span><b className={x.unclear ? 'muted' : x.d >= 0 ? 'up' : 'down'}>{points(x.d)}</b>
                    <span className="muted tiny">{x.note}{x.unclear ? '; not clearly different from no effect in this data, so read it as no clear change' : ''}</span></li>
                ))}
              </ul>
              {measured && measured.n >= MIN_N && (
                <p className="small" style={{ margin: '0.5rem 0 0' }}>
                  Check against the data: of the {measured.n} AIS ships of {cls.replace('-', ' to ')} in these passes, radar found <b>{pct(measured.rate)}</b> ({ci(measured.lo, measured.hi)}), in whatever weather they met.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  )
}
