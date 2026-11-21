import { useMemo, useState } from 'react'
import { Intro, Loading, LoadState, Tour, useTour, type TourStep } from '../components/ui'
import { fmt, pct, SEASON_LABEL, useJSON, wavesText, windText } from '../lib/data'
import { lengthClass } from '../lib/model'

type Round = { image: string; wind: number | null; waves: number | null; truth: 'ship' | 'not a ship'; length_m: number | null
  length_class?: string | null; ai_prob: number; ai_len: number; difficulty: 'easy' | 'hard'; note: string
  line?: boolean; season?: 'summer' | 'winter' }

// the length slider runs from 10 to 400 m on a log scale, so small boats get as much room as big ships
const L0 = 10, L1 = 400
const toLen = (v: number) => Math.round(L0 * Math.pow(L1 / L0, v / 100) / 5) * 5 || L0
const toPos = (len: number) => (100 * Math.log(len / L0)) / Math.log(L1 / L0)
const classText = (c: string) => c.replace('0-30 m', 'under 30 m').replace('-', ' to ')

const STEPS: TourStep[] = [
  { target: '#chip', title: 'A radar cut-out', text: 'This is what the radar saw around one detection: 640 m by 640 m, bright = strong echo. The sea state is shown next to it, and the white bar is 100 m long.' },
  { target: '#answer', title: 'Your call', text: 'Is it a ship? If yes, guess its length. Then press Reveal.' },
  { target: '#scores', title: 'The scoreboard', text: 'You against the AI and the plain detector (which calls everything a ship) over 10 rounds.' },
]

function shuffle<T>(a: T[], seed: number) {
  const r = [...a]
  let s = seed
  for (let i = r.length - 1; i > 0; i--) {
    s = (s * 9301 + 49297) % 233280
    const j = Math.floor((s / 233280) * (i + 1))
    ;[r[i], r[j]] = [r[j], r[i]]
  }
  return r
}

export default function Game() {
  const tour = useTour('game')
  const poolQ = useJSON<Round[]>('game.json')
  const pool = poolQ.data
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 100000))
  const rounds = useMemo(() => {
    if (!pool) return []
    // 5 easy and 5 hard, each drawn from both seasons' test passes in turn
    const take = (d: 'easy' | 'hard', k: number) => {
      const by = (season: string) => shuffle(pool.filter((r) => r.difficulty === d && (r.season ?? 'summer') === season), seed + k)
      const a = by('summer'), b = by('winter')
      const out: Round[] = []
      for (let j = 0; out.length < 5 && (j < a.length || j < b.length); j++) {
        if (j < a.length && out.length < 5) out.push(a[j])
        if (j < b.length && out.length < 5) out.push(b[j])
      }
      return out
    }
    return shuffle([...take('easy', 0), ...take('hard', 1)], seed + 2)
  }, [pool, seed])
  const [i, setI] = useState(0)
  const [guess, setGuess] = useState<'ship' | 'not a ship' | null>(null)
  const [len, setLen] = useState(60)
  const [shown, setShown] = useState(false)
  const blank = { you: 0, ai: 0, det: 0, done: 0, lenErr: [] as number[], aiLenErr: [] as number[], lenRounds: 0, youCls: 0, aiCls: 0 }
  const [score, setScore] = useState(blank)
  if (!pool) return <main className="page"><LoadState qs={[poolQ]} what="game" /></main>
  const r = rounds[i]
  // the final score comes after the last round's answer has been shown and "See the result" pressed
  const finished = rounds.length > 0 && i >= rounds.length
  const reveal = () => {
    if (!guess || !r) return
    const aiSays = r.ai_prob >= 0.5 ? 'ship' : 'not a ship'
    const s = { ...score, done: score.done + 1 }
    if (guess === r.truth) s.you++
    if (aiSays === r.truth) s.ai++
    if (r.truth === 'ship') s.det++
    // length: scored on the real ships with an AIS length; the right length class counts as right
    if (r.truth === 'ship' && r.length_m) {
      const cls = lengthClass(r.length_m)
      s.lenRounds++
      if (guess === 'ship') {
        s.lenErr = [...s.lenErr, Math.abs(len - r.length_m)]
        if (lengthClass(len) === cls) s.youCls++
      }
      s.aiLenErr = [...s.aiLenErr, Math.abs(r.ai_len - r.length_m)]
      if (lengthClass(r.ai_len) === cls) s.aiCls++
    }
    setScore(s)
    setShown(true)
  }
  const next = () => { setI(i + 1); setGuess(null); setShown(false); setLen(60) }
  const restart = () => { setSeed(seed + 7); setI(0); setGuess(null); setShown(false); setScore(blank) }
  const med = (a: number[]) => (a.length ? [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)] : null)
  return (
    <main className="page">
      <Intro
        what={<>A 10-round game. You see what the radar saw around a detection, and decide if it is a ship, like the AI does.</>}
        how={<>Look at the cut-out and the sea state, choose "Ship" or "Not a ship", and if it is a ship, guess its length. Press Reveal, then Next.</>}
        notice={<>Easy rounds are big, bright ships in calm water. Hard rounds are small boats, rough seas and false alarms that look like ships. Rounds come from summer and winter passes the AI never saw.</>}
      />
      <div className="grid2">
        <section className="card">
          {finished ? (
            <div>
              <h2>Final score</h2>
              <p>You got <b>{score.you} of {rounds.length}</b> right. The AI got <b>{score.ai}</b>, the plain detector <b>{score.det}</b>.</p>
              {score.lenRounds > 0 && <p className="small">Length class right on the {score.lenRounds} real ships: you <b>{score.youCls}</b>, the AI <b>{score.aiCls}</b>.{med(score.lenErr) != null ? <> Your typical length error: <b>{fmt(med(score.lenErr), 0)} m</b>; the AI's: <b>{fmt(med(score.aiLenErr), 0)} m</b>.</> : null}</p>}
              <button className="btn primary" onClick={restart}>Play again with new images</button>
            </div>
          ) : r ? (
            <div>
              <div className="row"><h2 style={{ margin: 0 }}>Round {i + 1} of {rounds.length}</h2><span className="spacer" /><span className="pill">{SEASON_LABEL[r.season ?? 'summer'].toLowerCase()}</span><span className={`pill ${r.difficulty === 'easy' ? 'ok' : 'warn'}`}>{r.difficulty}</span></div>
              <div className="row" style={{ alignItems: 'flex-start', marginTop: '0.75rem' }}>
                <div style={{ position: 'relative', flex: 'none' }}>
                  <img id="chip" className="game-chip" src={`/data/${r.image}`} alt="Radar cut-out around a detection" />
                  {/* the cut-out is 640 m across 256 px: 100 m is 40 px */}
                  <div className="chip-scale" data-tip="Scale: this bar is 100 m long"><span style={{ width: 256 / 6.4 }} />100 m</div>
                </div>
                <div className="stack" style={{ flex: 1, minWidth: 180 }}>
                  <div className="small"><b>Wind</b> {windText(r.wind)}<br /><b>Waves</b> {wavesText(r.waves)}</div>
                  <div id="answer" className="stack" style={{ gap: '0.5rem' }}>
                    <div className="choice">
                      {(['ship', 'not a ship'] as const).map((c) => (
                        <button key={c} className={`btn ${guess === c ? 'sel' : ''}`} disabled={shown} onClick={() => setGuess(c)}>
                          {c === 'ship' ? 'Ship' : 'Not a ship'}
                        </button>
                      ))}
                    </div>
                    {guess === 'ship' && (
                      <label className="small">
                        Length guess: <b>{len} m</b> <span className="muted">({classText(lengthClass(len))})</span>
                        <input type="range" min={0} max={100} step={0.5} value={toPos(len)} disabled={shown} aria-label="Length guess" onChange={(e) => setLen(toLen(+e.target.value))} style={{ width: '100%', accentColor: 'var(--you)' }} />
                      </label>
                    )}
                    {!shown
                      ? <button className="btn primary" disabled={!guess} onClick={reveal}>Reveal</button>
                      : <button className="btn primary" onClick={next}>{i + 1 < rounds.length ? 'Next round' : 'See the result'}</button>}
                  </div>
                </div>
              </div>
              {shown && (
                <div className="card" style={{ marginTop: '0.75rem', background: '#fafbfc' }}>
                  <p style={{ margin: 0 }}>
                    <b>Truth (from AIS):</b> {r.truth}{r.length_m ? `, length class ${classText(lengthClass(r.length_m))}` : ''}.{' '}
                    <b style={{ color: 'var(--ai)' }}>AI:</b> {pct(r.ai_prob)} ship{r.ai_prob >= 0.5 ? `, about ${fmt(r.ai_len, 0)} m (${classText(lengthClass(r.ai_len))})` : ''}.{' '}
                    <b style={{ color: 'var(--you)' }}>You:</b> {guess}{guess === 'ship' ? `, ${len} m (${classText(lengthClass(len))})` : ''}.
                  </p>
                  {r.truth === 'ship' && r.length_m && guess === 'ship' && (
                    <p className="small" style={{ margin: '0.35rem 0 0' }}>Length class: you {lengthClass(len) === lengthClass(r.length_m) ? <b>right</b> : 'not quite'}, the AI {lengthClass(r.ai_len) === lengthClass(r.length_m) ? <b>right</b> : 'not quite'}.</p>
                  )}
                  <p className="small muted" style={{ margin: '0.35rem 0 0' }}>{r.note}</p>
                </div>
              )}
            </div>
          ) : <Loading what="round" />}
        </section>
        <section className="card" id="scores">
          <h2>Scoreboard</h2>
          <p className="small muted" style={{ marginTop: 0 }}>Right answers so far, after {score.done} of {rounds.length} rounds. Length is scored on the real ships: the right length class (under 30, 30 to 60, 60 to 100, 100 to 200, over 200 m) counts as right.</p>
          {[
            { k: 'You', v: score.you, of: undefined as number | undefined, c: 'var(--you)', tip: 'Your correct answers so far: ship or not' },
            { k: 'AI', v: score.ai, c: 'var(--ai)', tip: 'The AI is right when its probability is on the right side of 50%' },
            { k: 'Plain detector', v: score.det, c: 'var(--detector)', tip: 'The plain detector calls every bright spot a ship' },
            { k: 'Length: you', v: score.youCls, of: score.lenRounds, c: 'var(--you)', tip: 'Real ships where your length guess was in the right length class' },
            { k: 'Length: AI', v: score.aiCls, of: score.lenRounds, c: 'var(--ai)', tip: "Real ships where the AI's length estimate was in the right length class" },
          ].map((s) => (
            <div key={s.k} className="score-row" data-tip={s.tip} style={{ margin: '0.4rem 0' }}>
              <span>{s.k}</span>
              <div className="bar"><div style={{ width: `${(100 * s.v) / Math.max(s.of ?? score.done, 1)}%`, background: s.c }} /></div>
              <b>{s.v} of {s.of ?? score.done}</b>
            </div>
          ))}
          <p className="small muted" style={{ marginTop: '1rem' }}>All cut-outs come from the summer test passes and the winter passes, which the AI never saw during training. Truth comes from AIS: a detection is a ship when an AIS position lies within 400 m, and "not a ship" when no AIS ship is within 1.5 km.</p>
          <p className="small muted">How to read a cut-out: ships are compact, very bright spots, often with a faint cross-shaped glow. Not a ship means something else: sea clutter, a wave crest or a fixed object. Rough sea gives a grainy, bright background with no single strong point. A long straight bright line is usually a bridge, cable or other structure.</p>
        </section>
      </div>
      <Tour id="game" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}
