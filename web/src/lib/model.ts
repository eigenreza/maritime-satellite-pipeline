import type { LogitModel, Odds, Summary } from './data'

/**
 * The detection model from the results: a logistic regression of "radar found this AIS ship" on
 * log2(length), wind and wave height (plus a winter indicator when the model has one), fitted on the
 * summer evaluation and winter passes. Point coefficients and the pass-bootstrap draws are exported by the
 * pipeline; nothing is refitted here. The draws give the 95% interval of every answer.
 */
const sigmoid = (x: number) => 1 / (1 + Math.exp(-x))

function linear(c: number[], lengthM: number, windMs: number, waveM: number, winter: number): number {
  const x = [Math.log2(Math.max(lengthM, 1)), windMs, waveM, winter]
  return c[0] + c.slice(1).reduce((a, b, i) => a + b * x[i], 0)
}

/** The model to use: the one with the season term when it exists (seasons: 0 summer, 1 winter). */
export function detectionModel(s: Summary): LogitModel | null {
  return s.models?.with_season ?? s.models?.without_season ?? null
}

export const hasSeason = (m: LogitModel) => m.coef.length >= 4

/** Chance that radar finds an AIS ship of this length (m) in this wind (m/s) and these waves (m). */
export function detectionChance(m: LogitModel, lengthM: number, windMs: number, waveM: number, winter = 0): number {
  return sigmoid(linear([m.intercept, ...m.coef], lengthM, windMs, waveM, hasSeason(m) ? winter : 0))
}

/** 95% interval of that chance, from the bootstrap draws (resampled passes). */
export function detectionInterval(m: LogitModel, lengthM: number, windMs: number, waveM: number, winter = 0): [number, number] {
  const v = m.draws.map((d) => sigmoid(linear(d, lengthM, windMs, waveM, hasSeason(m) ? winter : 0))).sort((a, b) => a - b)
  if (!v.length) return [NaN, NaN]
  const q = (p: number) => v[Math.min(v.length - 1, Math.max(0, Math.round(p * (v.length - 1))))]
  return [q(0.025), q(0.975)]
}

/** True when the 95% range of an odds ratio includes 1: the data cannot tell the effect from none. */
export const unclear = (r: Pick<Odds, 'lo' | 'hi'> | undefined) => !r || (r.lo <= 1 && r.hi >= 1)

/** The study's length class for a length in meters (labels as in the results). */
export function lengthClass(lengthM: number): string {
  if (lengthM < 30) return '0-30 m'
  if (lengthM < 60) return '30-60 m'
  if (lengthM < 100) return '60-100 m'
  if (lengthM < 200) return '100-200 m'
  return 'over 200 m'
}

export type ToolRange = { wave_max_m: number; wind_max_ms: number; length_min_m: number; length_max_m: number; ships_wind_over_15: number; ships_waves_over_3: number }
export type Preset = { label: string; wind: number; waves: number }

/** Weather presets for the ship tool, all inside the measured range. "Gale" only when the data holds
 * enough ships (minN) in winds over 15 m/s and the range reaches gale force; otherwise "Rough" at the edge. */
export function weatherPresets(r: ToolRange, minN: number): Preset[] {
  const r1 = (x: number) => Math.round(x * 10) / 10
  const out: Preset[] = [
    { label: 'Calm', wind: Math.min(2, r.wind_max_ms), waves: Math.min(0.2, r.wave_max_m) },
    { label: 'Moderate', wind: r1(r.wind_max_ms * 0.5), waves: r1(r.wave_max_m * 0.45) },
  ]
  if (r.wind_max_ms >= 17 && r.wave_max_m >= 3.5 && r.ships_wind_over_15 >= minN) out.push({ label: 'Gale', wind: 17, waves: 3.5 })
  else out.push({ label: 'Rough', wind: Math.floor(r.wind_max_ms), waves: Math.floor(r.wave_max_m * 10) / 10 })
  return out
}

/** Clamp the tool inputs to the measured range: the answer never comes from outside it. */
export const clampWeather = (r: ToolRange, wind: number, waves: number) =>
  ({ wind: Math.max(0, Math.min(wind, r.wind_max_ms)), waves: Math.max(0, Math.min(waves, r.wave_max_m)) })

const SHIPS: Preset2[] = [
  { label: 'Small fishing boat', len: 12 },
  { label: 'Fishing vessel', len: 30 },
  { label: 'Coastal ferry', len: 70 },
  { label: 'Cargo ship', len: 130 },
  { label: 'Cruise ship', len: 300 },
]
type Preset2 = { label: string; len: number }

/** Ship presets inside the measured length range; a preset beyond it is replaced by the edge of the range. */
export function shipPresets(r: Pick<ToolRange, 'length_min_m' | 'length_max_m'>): Preset2[] {
  const out = SHIPS.filter((x) => x.len >= r.length_min_m && x.len <= r.length_max_m)
  if (SHIPS[0].len < r.length_min_m) out.unshift({ label: 'Smallest measured', len: r.length_min_m })
  if (SHIPS[SHIPS.length - 1].len > r.length_max_m) out.push({ label: 'Largest measured', len: r.length_max_m })
  return out
}
