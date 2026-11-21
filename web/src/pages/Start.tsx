import { Link } from 'react-router-dom'
import { LoadState, Tour, useTour, type TourStep } from '../components/ui'
import { MIN_N, niceDay, oddsFor, pct, rowsFor, seaStateText, sizeMixRises, statsFor, useJSON, type Pass, type Summary } from '../lib/data'
import { count } from '../lib/text'

const STEPS: TourStep[] = [
  { target: '#answer', title: 'The short answer', text: 'The question this project asks, and what the data says. Every number on the site comes from the result files the pipeline wrote.' },
  { target: '#hero-map', title: 'One radar pass', text: 'Blue rings are ships the radar found, pink rings ships it missed, orange dots bright spots with no ship transponder nearby.' },
  { target: '#numbers', title: 'Three numbers', text: 'How many ships radar finds, how much ship size matters, and how well the AI filters false alarms.' },
  { target: '#open-map', title: 'Explore', text: 'The sea map lets you slide through every pass and click on anything. The menu has the detailed results, the AI and a game.' },
]

/** A rate in plain words: "About half", "About a third", ... */
function shareWords(r: number): string {
  if (r < 0.15) return 'Only a few'
  if (r > 0.88) return 'Almost all'
  const opts: [number, string][] = [[0.25, 'About a quarter'], [1 / 3, 'About a third'], [0.5, 'About half'], [2 / 3, 'About two thirds'], [0.75, 'About three quarters']]
  return opts.reduce((b, o) => (Math.abs(o[0] - r) < Math.abs(b[0] - r) ? o : b))[1]
}
const inTen = (r: number) => Math.round(10 * r)

export default function Start() {
  const tour = useTour('start')
  const sQ = useJSON<Summary>('summary.json')
  const s = sQ.data
  const { data: passes } = useJSON<Pass[]>('passes.json')
  const stormy = passes && s ? passes.find((p) => p.id === s.interesting_pass) : undefined
  const ok = s ? (rowsFor(s.by_length, 'both') ?? []).filter((r) => r.n >= MIN_N) : []
  const small = ok[0]
  const big = ok.find((r) => r.cls === '100-200 m') ?? ok[ok.length - 1]
  const odds = s ? oddsFor(s, 'both') : []
  const all = s ? statsFor(s, 'both') : undefined
  const sum = s?.seasons.summer, win = s?.seasons.winter
  const lengthTerm = odds.find((r) => r.term.startsWith('length'))
  const sizeDecides = !!lengthTerm && lengthTerm.lo > 1
  const cnn = s?.ml.scores.cnn
  const cnnW = s?.ml.seasons?.winter?.scores.cnn
  return (
    <main className="page">
      <section className="hero">
        <div id="answer">
          <div className="eyebrow">Free satellite radar · Bergen coast · {s ? `${niceMonth(s.periods.winter.start, true)} to ${niceMonth(s.periods.winter.end, true)} and ${niceMonth(s.periods.summer.start, false)} to ${niceMonth(s.periods.summer.end, true)}` : ''}</div>
          <h1>Can free satellite radar see the ships off Norway?</h1>
          {s && all && small && big ? (
            <p className="lead">
              <b>{shareWords(all.detection_overall.rate)} of them{sizeDecides ? ', and ship size decides most of it.' : '.'}</b>{' '}
              Radar found {inTen(big.rate)} in 10 ships of {big.cls.replace('-', ' to ')}, but only {inTen(small.rate)} in 10 boats under {small.cls.split('-')[1]}.{' '}
              {seaStateText(odds).replace('Once ship size is taken into account, ', 'Taking size into account, ')}
            </p>
          ) : <LoadState qs={[sQ]} what="results" />}
          <div className="cta">
            <Link to="/map" className="btn primary big" id="open-map">Explore the sea map →</Link>
            <Link to="/radar#ship-tool" className="btn big" id="ship-tool-link">Would radar see your ship?</Link>
          </div>
          <Link to="/map?tour=1" className="tour-link">New here? Take the one-minute tour of the map</Link>
        </div>
        <figure id="hero-map">
          <div className="hero-img"><img src="/data/hero.webp" alt="Radar image of the coast off Bergen with the ships the radar found and missed" /></div>
          <figcaption>
            {stormy && <span>{niceDay(stormy.time)} {new Date(stormy.time).getUTCFullYear()}, the roughest sea in this study: radar found {stormy.ais_detected} of {stormy.ais_visible} AIS ships.</span>}
            <span className="k2"><i className="sw ring" style={{ borderColor: 'var(--ais)' }} />found</span>
            <span className="k2"><i className="sw ring" style={{ borderColor: 'var(--missed)' }} />missed</span>
            <span className="k2"><i className="sw" style={{ background: 'var(--unmatched)' }} />no AIS ship nearby</span>
          </figcaption>
        </figure>
      </section>

      {s && all && small && big && (
        <section className="numbers" id="numbers">
          <div><div className="num">{pct(all.detection_overall.rate)}</div><p>of the ships that report their position by AIS were found by radar{sum && win ? ` (summer ${pct(sum.detection_overall.rate)}, winter ${pct(win.detection_overall.rate)})` : ''}</p></div>
          <div><div className="num">{pct(small.rate)} <small>vs</small> {pct(big.rate)}</div><p>boats under {small.cls.split('-')[1]} vs ships of {big.cls.replace('-', ' to ')}</p></div>
          {cnn?.false_alarms_removed && cnn?.ships_kept
            ? <div><div className="num">{pct(cnn.false_alarms_removed[0])}</div><p>of false alarms removed by the AI, while it keeps {pct(cnn.ships_kept[0])} of real ships{cnnW?.false_alarms_removed && cnnW?.ships_kept ? ` (summer test; in winter, which it never saw: ${pct(cnnW.false_alarms_removed[0])} and ${pct(cnnW.ships_kept[0])})` : ''}</p></div>
            : <div />}
        </section>
      )}
      {s && <p className="tiny muted" style={{ marginTop: '0.5rem' }}>The site holds {count(s.counts.passes, 'radar pass', 'radar passes')}: {s.counts.passes_summer} in summer and {s.counts.passes_winter} in winter. Detection rates come from the {count(s.counts.eval_passes + s.counts.winter_passes, 'pass', 'passes')} held back for checking ({s.counts.eval_passes} summer, {s.counts.winter_passes} winter); the other {count(s.counts.dev_passes, 'summer pass', 'summer passes')} were used to tune the detector. AI numbers come from the {count(s.ml.split.test_passes, 'summer test pass', 'summer test passes')}{s.ml.seasons?.winter ? ` and the ${count(s.ml.seasons.winter.passes, 'winter pass', 'winter passes')}` : ''}, which the AI never saw in training. Uncertainty ranges and details are on the results pages.</p>}

      <section style={{ padding: '1.75rem 0 0.5rem' }} id="how-it-works">
        <h2>How it works</h2>
        <div className="steps3">
          <div><div className="n">1</div><b>Radar looks</b><p>Sentinel-1 sees through cloud and darkness. Steel ships echo brightly against the sea.</p></div>
          <div><div className="n">2</div><b>Ships report</b><p>Larger ships broadcast their position (AIS). Each bright spot is checked against them.</p></div>
          <div><div className="n">3</div><b>Weather is added</b><p>Wind and waves at every ship show whether the sea state changes what radar finds.</p></div>
        </div>
      </section>

      {s && (
        <details className="more-findings">
          <summary>More findings</summary>
          <ul>
            {all && <li>{pct(all.unmatched_overall.rate)} of the radar detections had no AIS ship nearby: boats without AIS, structures, or false alarms.</li>}
            <li>In summer, optical satellites were blind on {pct(s.cloud.share)} of their days here because of cloud. Radar was never blind.</li>
            {sizeMixRises(rowsFor(s.size_by_wave, 'both')) && <li>In rough seas the raw numbers look better, mainly because mostly big ships were out then.</li>}
          </ul>
        </details>
      )}
      <Tour id="start" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}

function niceMonth(iso: string, year: boolean) {
  return new Date(iso).toLocaleDateString('en-GB', { month: 'short', ...(year ? { year: 'numeric' } : {}), timeZone: 'UTC' })
}

