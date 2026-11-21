import { Bar, BarChart, CartesianGrid, ErrorBar, Legend as RLegend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ClassTick } from '../components/charts'
import { Strictness } from '../components/Strictness'
import { Intro, LoadState, SeasonSwitch, Term, Tour, useSeason, useTour, type TourStep } from '../components/ui'
import { fmt, MIN_N, pct, useJSON, wavesText, windText, type Summary, type Triple } from '../lib/data'
import { shipsAndFalseAlarms } from '../lib/text'

type GalleryItem = { image: string; kind: string; ship_probability: number; truth: string; wind_speed: number; wave_hs: number
  detector_length_m: number; ais_length_class: string | null; ais_type_class: string | null; peak_vv_db: number; scr_db: number; season?: 'summer' | 'winter' }

const STEPS: TourStep[] = [
  { target: '#ai-what', title: 'What the AI does', text: 'Two sentences on what the model looks at and what it predicts.' },
  { target: '#scorecard', title: 'The scorecard', text: 'False alarms removed and real ships kept, overall and by sea state. Thin lines are 95% intervals.' },
  { target: '#strictness', title: 'Choose how strict', text: 'Move the cut-off: keep more real ships, or remove more false alarms. You cannot have both at once.' },
  { target: '#calibration', title: 'Can you trust the percentage?', text: 'If the AI says 80%, about 8 in 10 such detections should be ships. Points on the diagonal mean it is honest.' },
  { target: '#gallery', title: 'Where it goes wrong', text: 'The most confident mistakes, with a short note on why each case is hard.' },
]

const tri = (t?: Triple) => (t ? { v: t[0] * 100, err: [(t[0] - t[1]) * 100, (t[2] - t[0]) * 100] } : { v: 0, err: [0, 0] })

/** A short, case-specific reason why a gallery case is hard (uses its own sea state and ship size). */
function why(g: GalleryItem): string {
  const rough = (g.wind_speed ?? 0) >= 10 || (g.wave_hs ?? 0) >= 1.5
  if (g.kind === 'false_alarm_called_ship') {
    return rough
      ? 'A bright echo the AI took for a ship, with no AIS ship nearby. In this rough sea it may be a wave crest, or a boat without AIS.'
      : 'A bright echo the AI took for a ship, with no AIS ship nearby, in waves under 1.5 m: most likely a boat without AIS, which the AI may well be right about.'
  }
  const small = (g.ais_length_class ?? '').startsWith('under') || (g.ais_length_class ?? '').startsWith('0-')
  if (small) return 'A small AIS ship with a weak echo, close to the brightness of the sea around it.' + (rough ? ' The rough sea makes it harder still.' : '')
  return `An AIS ship of ${g.ais_length_class ?? 'unknown length'} the AI doubted. Possible reasons: its echo is smeared or split here, or the AIS position points at a nearby bright spot that is not the ship itself.`
}

export default function AI() {
  const tour = useTour('ai')
  const sQ = useJSON<Summary>('summary.json')
  const gQ = useJSON<GalleryItem[]>('gallery/gallery.json')
  const s = sQ.data
  const [season, setSeason] = useSeason('summer')
  const gallery = gQ.data
  if (!s) return <main className="page"><LoadState qs={[sQ]} what="results" /></main>
  const m = s.ml
  const sn = season === 'winter' ? 'winter' : 'summer'
  const set = m.seasons?.[sn]
  const sc = set?.scores ?? m.scores
  const cnn = sc.cnn
  const gb = sc.gradient_boosting
  const det = sc.detector
  const rough = (m.by_sea_state?.[sn] ?? []).filter((r) => r.model === 'cnn')
  const faAll = rough.reduce((a, r) => a + r.false_alarms, 0)
  const faCalm = rough[0]?.false_alarms ?? 0
  const faRough = faAll - faCalm
  const shownGallery = (gallery ?? []).filter((g) => (g.season ?? 'summer') === sn)
  const seasonName = sn === 'winter' ? 'winter passes' : 'summer test passes'
  const score = [
    { name: 'False alarms removed', ai: tri(cnn.false_alarms_removed), gb: tri(gb.false_alarms_removed), det: { v: 0, err: [0, 0] } },
    { name: 'Real ships kept', ai: tri(cnn.ships_kept), gb: tri(gb.ships_kept), det: { v: 100, err: [0, 0] } },
    { name: 'Precision', ai: tri(cnn.precision), gb: tri(gb.precision), det: tri(det.precision) },
  ].map((r) => ({ name: r.name, ai: r.ai.v, aiErr: r.ai.err, gb: r.gb.v, gbErr: r.gb.err, det: r.det.v, detErr: r.det.err }))
  // a share is shown only when its class has enough cases (MIN_N false alarms or ships)
  const sea = rough.map((r) => {
    const fa = r.false_alarms >= MIN_N
    const sk = r.ships >= MIN_N
    return {
      cls: r.wave_class, ships: r.ships, fas: r.false_alarms,
      removed: fa ? r.false_alarms_removed * 100 : null,
      removedErr: fa ? [(r.false_alarms_removed - r.fa_lo) * 100, (r.fa_hi - r.false_alarms_removed) * 100] : null,
      kept: sk ? r.ships_kept * 100 : null,
      keptErr: sk ? [(r.ships_kept - r.sk_lo) * 100, (r.sk_hi - r.ships_kept) * 100] : null, n: r.n,
    }
  })
  const seaSub = (v: string) => { const r = sea.find((x) => x.cls === v); return r ? shipsAndFalseAlarms(r.ships, r.fas) : '' }
  const calib = (cnn.calibration_curve ?? []).map((c) => ({ p: c.predicted * 100, o: c.observed * 100, n: c.n }))
  const lenRows = m.length_error?.[sn] ?? []
  const lenAll = lenRows.filter((r) => r.length_class === 'all')
  return (
    <main className="page">
      <Intro
        what={<>A small neural network that looks at a radar cut-out around each detection and says how likely it is to be a real ship, and how long it is.</>}
        how={<>Read the scorecard from left to right, then scroll to the calibration chart and the gallery of mistakes. Hover over bars for numbers.</>}
        notice={<>{cnn?.false_alarms_removed && cnn?.ships_kept ? `On the ${seasonName} the AI removed ${pct(cnn.false_alarms_removed[0])} of false alarms and kept ${pct(cnn.ships_kept[0])} of real ships. ` : ''}{rough.length ? `${faCalm} of the ${faAll} false alarms came in waves of ${rough[0].wave_class}; ${faRough < MIN_N ? 'rougher seas had too few to judge.' : `${faRough} came in rougher seas.`}` : ''}</>}
      />
      <div className="season-bar"><SeasonSwitch value={season} onChange={setSeason} options={['summer', 'winter']} label="Test set" /><span className="tiny muted">{sn === 'winter' ? `Winter: ${set?.passes ?? 0} passes from another season, scored with the summer model as it was, no retraining.` : 'Summer: the latest summer passes, which the model never saw.'}</span></div>
      <section className="card" id="ai-what" style={{ marginBottom: '1rem' }}>
        <h2>What the model does</h2>
        <p>The model sees a 640 m by 640 m radar cut-out around each detection (both radar channels, VV and VH) together with the detector's own measurements of the bright spot and the wind and waves there. It learned from {m.split.train_n.toLocaleString('en-US')} detections on the earliest passes, where AIS told us which bright spots were real ships, and it is scored here only on passes it never saw: the {m.split.test_passes} latest summer passes, and {m.seasons?.winter?.passes ?? 0} winter passes it was never adapted to.</p>
        <p className="small muted">CNN, convolutional neural network: a model that learns visual patterns directly from images. Its probabilities are calibrated on separate passes so that "80% ship" means about 8 in 10. {set ? `Shown here, ${seasonName}: ${set.ships} ships and ${set.not_ships} false alarms; ${set.ambiguous_excluded} unclear cases left out.` : `Test passes: ${m.split.test_ships} ships and ${m.split.test_not_ships} false alarms; ${m.split.ambiguous_excluded} unclear cases left out.`}</p>
      </section>

      <div className="grid2" id="scorecard">
        <section className="card">
          <h2>Scorecard ({seasonName})</h2>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={score} margin={{ left: -14, right: 8, top: 8 }}>
              <CartesianGrid stroke="#eef1f4" vertical={false} />
              <XAxis dataKey="name" fontSize={11} />
              <YAxis domain={[0, 100]} unit="%" fontSize={11} />
              <Tooltip formatter={(v, n) => [`${Number(v).toFixed(0)}%`, String(n)]} />
              <RLegend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="det" name="Plain detector" fill="var(--detector)" radius={[5, 5, 0, 0]} isAnimationActive={false}><ErrorBar dataKey="detErr" width={5} stroke="#333" /></Bar>
              <Bar dataKey="gb" name="Simple model (features)" fill="#b197fc" radius={[5, 5, 0, 0]} isAnimationActive={false}><ErrorBar dataKey="gbErr" width={5} stroke="#333" /></Bar>
              <Bar dataKey="ai" name="AI (image model)" fill="var(--ai)" radius={[5, 5, 0, 0]} isAnimationActive={false}><ErrorBar dataKey="aiErr" width={5} stroke="#333" /></Bar>
            </BarChart>
          </ResponsiveContainer>
          <table className="data small">
            <tbody>
              <tr><td><Term k="rocauc">ROC AUC</Term> (1 = perfect ranking)</td><td><b>{fmt(cnn.roc_auc?.[0], 3)}</b> <span className="muted">({fmt(cnn.roc_auc?.[1], 3)} to {fmt(cnn.roc_auc?.[2], 3)})</span></td><td className="muted">simple model {fmt(gb.roc_auc?.[0], 3)}</td></tr>
              <tr><td><Term k="prauc">Precision-recall AUC</Term></td><td><b>{fmt(cnn.pr_auc?.[0], 3)}</b> <span className="muted">({fmt(cnn.pr_auc?.[1], 3)} to {fmt(cnn.pr_auc?.[2], 3)})</span></td><td className="muted">simple model {fmt(gb.pr_auc?.[0], 3)}</td></tr>
            </tbody>
          </table>
          <p className="tiny muted">Precision: of the detections that are kept, the share that are real ships. ROC AUC: how well the score ranks real ships above false alarms.</p>
        </section>
        <section className="card">
          <h2>In calm and rough seas</h2>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={sea} margin={{ left: -14, right: 8, top: 8, bottom: 8 }}>
              <CartesianGrid stroke="#eef1f4" vertical={false} />
              <XAxis dataKey="cls" interval={0} height={36} tick={<ClassTick sub={seaSub} />} />
              <YAxis domain={[0, 100]} unit="%" fontSize={11} />
              <Tooltip formatter={(v, n) => [`${Number(v).toFixed(0)}%`, String(n)]} labelFormatter={(l) => `waves ${l}`} />
              <RLegend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="removed" name="False alarms removed" fill="var(--ai)" radius={[5, 5, 0, 0]} isAnimationActive={false}><ErrorBar dataKey="removedErr" width={5} stroke="#333" /></Bar>
              <Bar dataKey="kept" name="Real ships kept" fill="var(--matched)" radius={[5, 5, 0, 0]} isAnimationActive={false}><ErrorBar dataKey="keptErr" width={5} stroke="#333" /></Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="small muted">By significant wave height at the detection. A bar is left out when its class has fewer than {MIN_N} cases.{faRough < MIN_N ? ' Here the rough classes had too few false alarms to say how well the AI removes them.' : ''} The plain detector keeps every false alarm in every sea state.</p>
        </section>
      </div>

      {cnn?.calibration_curve && <Strictness bins={cnn.calibration_curve} threshold={m.threshold} />}

      <div className="grid2" style={{ marginTop: '1rem' }}>
        <section className="card" id="calibration">
          <h2>Calibration</h2>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={calib} margin={{ left: -14, right: 8, top: 8 }}>
              <CartesianGrid stroke="#eef1f4" />
              <XAxis type="number" dataKey="p" domain={[0, 100]} unit="%" fontSize={11} name="AI says" />
              <YAxis type="number" domain={[0, 100]} unit="%" fontSize={11} />
              <Tooltip formatter={(v) => [`${Number(v).toFixed(0)}%`, 'actually ships']} labelFormatter={(l) => `AI says ${Number(l).toFixed(0)}% ship`} />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="#adb5bd" strokeDasharray="4 4" />
              <Line dataKey="o" stroke="var(--ai)" strokeWidth={2} dot={{ r: 4, fill: 'var(--ai)' }} name="actually ships" isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
          <p className="small"><Term k="ece">Expected calibration error</Term>: <b>{pct(cnn.ece ?? 0, 1)}</b> (average gap between what the AI says and what happens). <Term k="temperature">Temperature</Term> used: {fmt(m.temperature, 2)}.</p>
        </section>
        <section className="card">
          <h2>How long is the ship?</h2>
          <table className="data">
            <thead><tr><th>Method</th><th>Typical error</th><th>In percent</th></tr></thead>
            <tbody>
              {lenAll.map((r) => (
                <tr key={r.model}><td>{r.model === 'cnn' ? 'AI (image model)' : r.model === 'detector' ? 'Size of the bright spot' : 'Simple model'}</td>
                  <td><b>{fmt(r.mae_m, 0)} m</b> <span className="muted small">({fmt(r.lo, 0)} to {fmt(r.hi, 0)})</span></td><td>{pct(r.mape)}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">Median absolute error against the AIS length of matched ships on the {seasonName}. By length class:</p>
          <LengthByClass rows={lenRows.filter((r) => r.model === 'cnn' && r.length_class !== 'all')} />
        </section>
      </div>

      <section className="card" id="gallery" style={{ marginTop: '1rem' }}>
        <h2>Error gallery: the most confident mistakes</h2>
        <p className="small muted">Cases where the AI was most sure and still disagreed with AIS. Top: it said "ship" with no AIS ship nearby. Bottom: it doubted a real AIS ship. AIS is the yardstick, so some top cases may be boats without AIS that the AI got right.</p>
        {!gallery ? <LoadState qs={[gQ]} what="gallery" /> : (
          <div className="grid3" style={{ marginTop: '0.75rem' }}>
            {shownGallery.map((g) => (
              <figure key={g.image} style={{ margin: 0 }} className="row">
                <img src={`/data/${g.image}`} alt={`Radar cut-out, ${g.truth}`} width={110} height={110} style={{ imageRendering: 'pixelated', borderRadius: 8 }} />
                <figcaption className="small" style={{ flex: 1, minWidth: 140 }}>
                  <b>AI: {pct(g.ship_probability)} ship</b><br />
                  Truth: {g.kind === 'false_alarm_called_ship' ? 'no AIS ship nearby (counted as a false alarm)' : `AIS ship${g.ais_type_class ? ` (${g.ais_type_class}, ${g.ais_length_class})` : ''}`}<br />
                  <span className="muted">wind {windText(g.wind_speed)}, waves {wavesText(g.wave_hs)}</span><br />
                  <span className="tiny muted">{why(g)}</span>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </section>
      <Tour id="ai" steps={STEPS} open={tour.open} onClose={tour.close} />
    </main>
  )
}

function LengthByClass({ rows }: { rows: { length_class: string; mae_m: number; n: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={150}>
      <LineChart data={rows} margin={{ left: -14, right: 8, top: 8 }}>
        <CartesianGrid stroke="#eef1f4" />
        <XAxis dataKey="length_class" fontSize={11} />
        <YAxis unit=" m" fontSize={11} />
        <Tooltip formatter={(v) => [`${Number(v).toFixed(0)} m`, 'typical error']} />
        <Line dataKey="mae_m" stroke="var(--ai)" strokeWidth={2} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}
