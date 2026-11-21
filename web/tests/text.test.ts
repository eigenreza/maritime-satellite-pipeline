// Every generated sentence renders correctly with 1 and with 2. Run: node --test web/tests/
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { casesLabel, count, passStoryText, shipsAndFalseAlarms, startupAlertsText, tenRange } from '../src/lib/text.ts'

const bad = /\b1 (ships|spots|false alarms|alerts|were|passes|detections)\b|\b1 bright spots\b|\b2 (ship|false alarm|bright spot|was)\b(?!s)/

test('count picks singular and plural', () => {
  assert.equal(count(1, 'ship'), '1 ship')
  assert.equal(count(2, 'ship'), '2 ships')
})

test('pass summary with 1 and with 2 of everything', () => {
  for (const n of [1, 2]) {
    const s = passStoryText({ ais: n + 3, found: 3, missed: n, missedSmall: n, missedBig: 0, dets: 5, noAis: n, noAisRejected: n })
    assert.doesNotMatch(s, bad, s)
    const mixed = passStoryText({ ais: 10, found: 10 - 2 * n, missed: 2 * n, missedSmall: n, missedBig: n, dets: 5, noAis: n, noAisRejected: 1 })
    assert.doesNotMatch(mixed, bad, mixed)
  }
  assert.match(passStoryText({ ais: 5, found: 4, missed: 1, missedSmall: 1, missedBig: 0, dets: 1, noAis: 1, noAisRejected: 1 }), /The one it missed was under 60 m/)
})

test('labels and alert sentence with 1 and with 2', () => {
  for (const n of [1, 2]) {
    assert.doesNotMatch(shipsAndFalseAlarms(n, n), bad)
    assert.doesNotMatch(startupAlertsText(n), /All 1 alerts/)
    assert.doesNotMatch(casesLabel(n, 'ships', true), bad)
  }
  assert.equal(shipsAndFalseAlarms(1, 1), '1 ship, 1 false alarm')
})

test('95% range in tenths rounds outward and contains the interval', () => {
  assert.equal(tenRange(0.35, 0.48), 'between 3 and 5 in 10')
  assert.equal(tenRange(0.3, 0.5), 'between 3 and 5 in 10')
  assert.equal(tenRange(0, 1), 'between 0 and 10 in 10')
  for (let i = 0; i < 200; i++) {
    const lo = Math.random() * 0.9, hi = lo + Math.random() * (1 - lo)
    const [a, b] = tenRange(lo, hi).match(/\d+/g)!.map(Number)
    assert.ok(a / 10 <= lo + 1e-9 && b / 10 >= hi - 1e-9, `${lo} ${hi} -> ${a} ${b}`)
  }
})
