// The ship tool never answers outside the measured range. Run: npm test
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { test } from 'node:test'
import { clampWeather, detectionChance, detectionInterval, shipPresets, weatherPresets, type ToolRange } from '../src/lib/model.ts'

const calm: ToolRange = { wave_max_m: 2.1, wind_max_ms: 11.5, length_min_m: 10, length_max_m: 290, ships_wind_over_15: 0, ships_waves_over_3: 0 }
const stormy: ToolRange = { wave_max_m: 5.2, wind_max_ms: 19, length_min_m: 10, length_max_m: 290, ships_wind_over_15: 140, ships_waves_over_3: 90 }

test('presets stay inside the range, and "Gale" needs data', () => {
  for (const r of [calm, stormy, { ...stormy, ships_wind_over_15: 3 }]) {
    for (const p of weatherPresets(r, 10)) {
      assert.ok(p.wind >= 0 && p.wind <= r.wind_max_ms, `${p.label} wind ${p.wind} > ${r.wind_max_ms}`)
      assert.ok(p.waves >= 0 && p.waves <= r.wave_max_m, `${p.label} waves ${p.waves} > ${r.wave_max_m}`)
    }
  }
  assert.ok(!weatherPresets(calm, 10).some((p) => p.label === 'Gale'))
  assert.ok(!weatherPresets({ ...stormy, ships_wind_over_15: 3 }, 10).some((p) => p.label === 'Gale'))
  assert.ok(weatherPresets(stormy, 10).some((p) => p.label === 'Gale'))
})

test('ship presets stay inside the measured lengths', () => {
  for (const r of [calm, { ...calm, length_min_m: 15, length_max_m: 250 }]) {
    const ps = shipPresets(r)
    assert.ok(ps.length >= 3)
    for (const p of ps) assert.ok(p.len >= r.length_min_m && p.len <= r.length_max_m, `${p.label} ${p.len}`)
  }
})

test('inputs are clamped to the range', () => {
  assert.deepEqual(clampWeather(calm, 30, 9), { wind: calm.wind_max_ms, waves: calm.wave_max_m })
  assert.deepEqual(clampWeather(calm, -1, -1), { wind: 0, waves: 0 })
})

// the exported demo data, when present: presets inside its range, intervals around the estimate
const f = new URL('../public/data/summary.json', import.meta.url)
test('exported summary: tool range and model answers', { skip: !existsSync(f) }, () => {
  const s = JSON.parse(readFileSync(f, 'utf-8'))
  const r: ToolRange = s.tool_range
  for (const p of weatherPresets(r, 10)) assert.ok(p.wind <= r.wind_max_ms && p.waves <= r.wave_max_m, p.label)
  for (const p of shipPresets(r)) assert.ok(p.len >= r.length_min_m && p.len <= r.length_max_m, p.label)
  const m = s.models?.with_season ?? s.models?.without_season
  if (!m) return
  for (const winter of [0, 1]) {
    for (const len of [r.length_min_m, 50, r.length_max_m]) {
      const { wind, waves } = clampWeather(r, 99, 99)
      const p = detectionChance(m, len, wind, waves, winter)
      const [lo, hi] = detectionInterval(m, len, wind, waves, winter)
      assert.ok(p >= 0 && p <= 1 && lo <= hi && lo >= 0 && hi <= 1, `${len} m: ${p} (${lo} to ${hi})`)
    }
  }
})
