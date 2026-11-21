import { useCallback, useEffect, useState } from 'react'

const cache = new Map<string, Promise<unknown>>()
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** One request with a time limit; the free server can take 20 to 30 seconds to wake up. */
async function fetchOnce(url: string, timeoutMs: number): Promise<unknown> {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), timeoutMs)
  try {
    const r = await fetch(url, { signal: ctl.signal })
    if (!r.ok) throw Object.assign(new Error(`${r.status} ${url}`), { status: r.status })
    return await r.json()
  } finally {
    clearTimeout(t)
  }
}

/**
 * Fetch a data file once per page view. Network errors, time-outs and server errors are retried with
 * a growing pause; a missing file (404) is not. A failed request is never cached, so "Try again" works.
 */
export function getJSON<T>(path: string): Promise<T> {
  const url = path.startsWith('/') ? path : `/data/${path}`
  if (!cache.has(url)) {
    const p = (async () => {
      const waits = [0, 1500, 4000, 8000]
      let last: unknown = null
      for (const w of waits) {
        if (w) await sleep(w)
        try {
          return await fetchOnce(url, 35000)
        } catch (e) {
          last = e
          if ((e as { status?: number }).status === 404) break
        }
      }
      cache.delete(url)
      throw last
    })()
    cache.set(url, p)
  }
  return cache.get(url) as Promise<T>
}

export type Query<T> = { data: T | null; error: string | null; slow: boolean; retry: () => void }

/** Data for a component, with a "slow" flag after a few seconds and a retry for errors. */
export function useJSON<T>(path: string | null): Query<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [slow, setSlow] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!path) return
    let live = true
    setData(null)
    setError(null)
    setSlow(false)
    const t = setTimeout(() => live && setSlow(true), 3500)
    getJSON<T>(path)
      .then((d) => { if (live) setData(d) })
      .catch((e) => { if (live) setError(String(e)) })
      .finally(() => clearTimeout(t))
    return () => { live = false; clearTimeout(t) }
  }, [path, attempt])
  const retry = useCallback(() => setAttempt((a) => a + 1), [])
  return { data, error, slow, retry }
}

export type Interval = { rate: number; lo: number; hi: number; n: number; events?: number }

export type Pass = {
  id: string; time: string; label: string; platform: string; orbit: number; direction: string; engine: string
  bounds: [number, number][]; image: string; image_lite?: string; waves_image: string; wave_bounds: [number, number][]; wind: string
  detections: number; matched: number; unmatched: number; ai_removed: number
  ais_visible: number; ais_detected: number; wind_mean: number; wave_mean: number; wave_max: number
  s2_day: string | null; sst_day: string | null; split: string; season?: 'summer' | 'winter'
  /** sea state over open sea (wave-model cells at least 3 km from land) at the pass time */
  sea_waves?: number | null; sea_waves_max?: number | null; sea_wind?: number | null; sea_class?: string | null
  ai_errors?: number; ai_error_share?: number | null; line_shaped?: number
}

export type Season = 'summer' | 'winter' | 'both'
export const SEASON_LABEL: Record<Season, string> = { summer: 'Summer', winter: 'Winter', both: 'Both' }
type Row = Interval & { cls: string }
type BySeason<T> = Partial<Record<Season, T>>
export type Odds = { term: string; odds_ratio: number; lo: number; hi: number; n: number }
export type LogitModel = { terms: string[]; intercept: number; coef: number[]; n: number
  typical: { length_m: number; wind_ms: number }; draws: number[][] }
export type SeasonStats = { passes: number; ais_ships: number; detections: number
  detection_overall: Interval; unmatched_overall: Interval
  missing_sea_state: { ais: { rows: number; no_wave: number; no_wind: number }; detections: { rows: number; no_wave: number; no_wind: number } } }

export type Summary = {
  periods: { summer: Period; winter: Period }
  period: Period
  area: { name: string; bbox: number[] }
  counts: { passes: number; passes_summer: number; passes_winter: number; detections: number; ais_ships_visible: number
    eval_passes: number; winter_passes: number; dev_passes: number; stac_items: number }
  seasons: BySeason<SeasonStats>
  by_length: BySeason<Row[]>
  by_wind: BySeason<Row[]>
  by_wave: BySeason<Row[]>
  unmatched_by_wave: BySeason<Row[]>
  unmatched_by_wind: BySeason<Row[]>
  size_by_wave: BySeason<SizeRow[]>
  size_by_wind: BySeason<SizeRow[]>
  logistic: { with_season?: Odds[]; without_season?: Odds[] }
  models: { with_season?: LogitModel; without_season?: LogitModel }
  tool_range: { wave_max_m: number; wind_max_ms: number; length_min_m: number; length_max_m: number
    ships_wind_over_15: number; ships_waves_over_3: number }
  cloud: { share: number; lo: number; hi: number; days: number; blind_days: number; threshold: number; mean_cloud_share: number }
  ml: MLScores
  interesting_pass: string
  stories: { key: string; title: string; pass: string }[]
  snapshot: string
  wave_curve: { hs: number; rate: number; lo: number; hi: number }[]
  typical_ship?: { length_m: number; wind_ms: number } | null
}
export type Period = { start: string; end: string; weeks: number }

export type SizeRow = { cls: string; n: number; median_length_m: number; share_over_60m: number }

export type Triple = [number, number, number]
export type ScoreSet = Record<string, {
  precision: Triple; recall: Triple; roc_auc?: Triple; pr_auc?: Triple
  false_alarms_removed?: Triple; ships_kept?: Triple; ece?: number
  calibration_curve?: { bin: number; predicted: number; observed: number; n: number }[]
}>
export type SeaRow = { wave_class: string; model: string; n: number; ships: number; false_alarms: number
  false_alarms_removed: number; fa_lo: number; fa_hi: number; ships_kept: number; sk_lo: number; sk_hi: number }
export type LenRow = { model: string; length_class: string; n: number; mae_m: number; lo: number; hi: number; mape: number }
export type MLScores = {
  split: Record<string, number>
  temperature: number
  threshold: number
  scores: ScoreSet
  seasons: { summer?: { passes: number; ships: number; not_ships: number; ambiguous_excluded: number; scores: ScoreSet }
    winter?: { passes: number; ships: number; not_ships: number; ambiguous_excluded: number; scores: ScoreSet } }
  by_sea_state: Partial<Record<'summer' | 'winter', SeaRow[]>>
  length_error: Partial<Record<'summer' | 'winter', LenRow[]>>
}

/** The rows of one season, or of both seasons combined when the season has none. */
export const rowsFor = <T,>(m: BySeason<T> | undefined, season: Season): T | undefined => m?.[season] ?? m?.both

/** Totals of one season (or both together). */
export const statsFor = (s: Summary, season: Season): SeasonStats | undefined => s.seasons?.[season] ?? s.seasons?.both

/** The logistic regression table that fits the season shown: pooled without season term for both. */
export const oddsFor = (s: Summary, season: Season): Odds[] =>
  (season === 'both' ? s.logistic.without_season : s.logistic.with_season) ?? s.logistic.with_season ?? s.logistic.without_season ?? []

/** Wave height for a card: missing stays missing, near-zero is named as sheltered water. */
export const wavesText = (v: unknown) => typeof v !== 'number' || Number.isNaN(v) ? 'no wave data here' : v < 0.1 ? 'under 0.1 m (sheltered water)' : `${v.toFixed(1)} m`
export const windText = (v: unknown) => typeof v !== 'number' || Number.isNaN(v) ? 'no wind data here' : `${v.toFixed(1)} m/s`

export const pct = (x: number | null | undefined, d = 0) => (x == null || Number.isNaN(x) ? 'n/a' : `${(100 * x).toFixed(d)}%`)
export const fmt = (x: number | null | undefined, d = 1) => (x == null || Number.isNaN(x) ? 'n/a' : x.toFixed(d))
export const ci = (lo: number, hi: number, d = 0) => `${pct(lo, d)} to ${pct(hi, d)}`

/** Classes with fewer cases than this get no rate or interval in charts and texts: too few to tell. */
export const MIN_N = 10

/** Plain-language reading of the logistic regression for wind and waves (data-driven, no fixed claims).
 * `roughCases` is the number of ships seen in waves of 3 m or more; with few, the sentence says so. */
export function seaStateText(logistic: Pick<Odds, 'term' | 'odds_ratio' | 'lo' | 'hi'>[] | undefined, roughCases?: number): string {
  const part = (key: string, name: string) => {
    const r = (logistic ?? []).find((x) => x.term.startsWith(key))
    if (!r) return null
    if (r.lo <= 1 && r.hi >= 1) return null
    return `stronger ${name} made detection ${r.odds_ratio < 1 ? 'less' : 'more'} likely`
  }
  const parts = [part('wind', 'wind'), part('wave', 'waves')].filter(Boolean)
  const few = roughCases != null && roughCases < 30 ? '; rough-sea cases were few' : ''
  return parts.length
    ? `Once ship size is taken into account, ${parts.join(' and ')}.`
    : `Once ship size is taken into account, wind and waves showed no clear effect${few}.`
}

/** True when the typical ship out at sea was clearly bigger in the roughest class with enough cases than in the calmest. */
export function sizeMixRises(rows?: { n: number; median_length_m: number }[]): boolean {
  const ok = (rows ?? []).filter((r) => r.n >= MIN_N)
  return ok.length >= 2 && ok[ok.length - 1].median_length_m > ok[0].median_length_m * 1.15
}

/** Direction of the no-AIS share from calm to rough sea, using only classes with enough cases. */
export function unmatchedTrend(rows: { rate: number; n: number }[]): 'up' | 'down' | 'flat' {
  const ok = rows.filter((r) => r.n >= MIN_N)
  if (ok.length < 2) return 'flat'
  const d = ok[ok.length - 1].rate - ok[0].rate
  return d > 0.05 ? 'up' : d < -0.05 ? 'down' : 'flat'
}

export function niceTime(iso: string) {
  const t = new Date(iso)
  return t.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC'
}
export function niceDay(iso: string) {
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
}
