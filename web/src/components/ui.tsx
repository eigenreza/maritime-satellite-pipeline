import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useSearchParams } from 'react-router-dom'
import { SEASON_LABEL, type Season } from '../lib/data'

export const REPO_URL = 'https://github.com/eigenreza/maritime-satellite-pipeline'
export const AUTHOR = 'Reza Azad Gholami'

const NAV = [
  { to: '/', label: 'Start', tip: 'The question, the answer and how it works' },
  { to: '/map', label: 'Sea map', tip: 'Radar images, ships, clouds, wind and waves on a map with a time slider' },
  { to: '/radar', label: 'What radar sees', tip: 'How often radar finds ships, by ship size, wind and waves' },
  { to: '/ai', label: 'The AI', tip: 'A model that tells real ships from false alarms, and how well it works' },
  { to: '/game', label: 'Play the game', tip: 'A 10-round game: can you spot ships in radar images?' },
]
const MORE = [
  { to: '/clouds', label: 'Clouds vs radar', tip: 'How often optical satellites are blind because of cloud' },
  { to: '/ops', label: 'Pipeline', tip: 'The data pipeline: runs, retries, failures and data freshness' },
  { to: '/catalogue', label: 'Catalogue', tip: 'Search every data product the pipeline made' },
  { to: '/built', label: 'How it is built', tip: 'Architecture, data formats and tools' },
]

export function Header({ onHelp }: { onHelp: () => void }) {
  const [open, setOpen] = useState(false)
  const [more, setMore] = useState(false)
  const loc = useLocation()
  useEffect(() => { setOpen(false); setMore(false) }, [loc.pathname])
  const inMore = MORE.some((m) => m.to === loc.pathname)
  return (
    <header className="header">
      <NavLink to="/" className="brand" data-tip="Back to the start page">
        <img src="/favicon.svg" alt="" />
        <div style={{ minWidth: 0 }}>
          <div className="brand-name">Ships, radar and sea state</div>
          <div className="brand-sub">Free satellite data shows what is at sea off western Norway, in any weather</div>
        </div>
      </NavLink>
      <nav className={`nav ${open ? 'open' : ''}`} aria-label="Pages">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'} data-tip={n.tip}>{n.label}</NavLink>
        ))}
        <div className={`more ${more ? 'open' : ''}`} onMouseLeave={() => setMore(false)}>
          <button className={`more-btn ${inMore ? 'active' : ''}`} onClick={() => setMore(!more)} aria-expanded={more}>More ▾</button>
          <div className="more-menu">
            {MORE.map((n) => (
              <NavLink key={n.to} to={n.to} data-tip={n.tip}>{n.label}</NavLink>
            ))}
          </div>
        </div>
      </nav>
      <div className="header-tools">
        <button className="icon-btn menu-btn" onClick={() => setOpen(!open)} aria-label="Menu" data-tip="Show the page menu">☰</button>
        <button className="icon-btn" onClick={onHelp} aria-label="Help" data-tip="A short guided tour of this page" id="help-btn">?</button>
        <a className="icon-btn" href={REPO_URL} target="_blank" rel="noreferrer" data-tip="Source code, documentation and data notes on GitHub" id="repo-link">
          <GitHubIcon /> <span className="btn-label">GitHub</span>
        </a>
      </div>
    </header>
  )
}

export function Footer() {
  return (
    <footer className="footer">
      <span>Developed by <strong>{AUTHOR}</strong> · <a href={REPO_URL} target="_blank" rel="noreferrer">github.com/eigenreza/maritime-satellite-pipeline</a></span>
      <span className="tiny">Contains modified Copernicus Sentinel data 2025 and 2026 · AIS: Kystverket (NLOD) · Weather and waves: MET Norway, ECMWF (CC BY 4.0) · Coastline © Kartverket · Structures © OpenStreetMap contributors (ODbL)</span>
    </footer>
  )
}

export function GitHubIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  )
}

/** One line on what the page is; how to use it and what to notice open on request. */
export function Intro({ what, how, notice }: { what: ReactNode; how: ReactNode; notice: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <section className={`intro ${open ? 'open' : ''}`} id="page-intro">
      <div className="intro-line">
        <span className="v">{what}</span>
        <button className="intro-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide tips ▴' : 'How to read this page ▾'}</button>
      </div>
      {open && (
        <div className="intro-more">
          <div><div className="k">How to use it</div><div className="v">{how}</div></div>
          <div><div className="k">What to notice</div><div className="v">{notice}</div></div>
        </div>
      )}
    </section>
  )
}

/** Global tooltip: any element with data-tip="..." shows a small label on hover or focus. */
export function TooltipLayer() {
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null)
  useEffect(() => {
    const show = (e: Event) => {
      const el = (e.target as HTMLElement)?.closest?.('[data-tip]') as HTMLElement | null
      if (!el) return
      const r = el.getBoundingClientRect()
      const x = Math.min(window.innerWidth - 290, Math.max(8, r.left))
      const y = r.bottom + 8 > window.innerHeight - 60 ? r.top - 48 : r.bottom + 8
      setTip({ text: el.dataset.tip || '', x, y })
    }
    const hide = () => setTip(null)
    document.addEventListener('mouseover', show)
    document.addEventListener('focusin', show)
    document.addEventListener('mouseout', hide)
    document.addEventListener('focusout', hide)
    document.addEventListener('scroll', hide, true)
    return () => {
      document.removeEventListener('mouseover', show)
      document.removeEventListener('focusin', show)
      document.removeEventListener('mouseout', hide)
      document.removeEventListener('focusout', hide)
      document.removeEventListener('scroll', hide, true)
    }
  }, [])
  if (!tip || !tip.text) return null
  return <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div>
}

export type TourStep = { target: string; title: string; text: string }

/** Short guided tour. Opens only on request: the "?" button, or a link with ?tour=1. */
export function Tour({ steps, open, onClose }: { id: string; steps: TourStep[]; open: boolean; onClose: () => void }) {
  const [i, setI] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  useEffect(() => { if (open) setI(0) }, [open])
  useLayoutEffect(() => {
    if (!open) return
    const update = () => {
      const el = document.querySelector(steps[i]?.target)
      if (el) {
        el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
        setRect(el.getBoundingClientRect())
      } else setRect(null)
    }
    update()
    const t = setTimeout(update, 350)
    window.addEventListener('resize', update)
    return () => { clearTimeout(t); window.removeEventListener('resize', update) }
  }, [open, i, steps])
  if (!open || !steps.length) return null
  const s = steps[i]
  const close = () => onClose()
  const pad = 6
  const box = rect
    ? {
        left: Math.min(window.innerWidth - 372, Math.max(12, rect.left)),
        top: rect.bottom + 14 + 200 < window.innerHeight ? rect.bottom + 14 : Math.max(12, rect.top - 214),
      }
    : { left: window.innerWidth / 2 - 180, top: window.innerHeight / 3 }
  return (
    <>
      <div className="tour-shade" />
      {rect && <div className="tour-hole" style={{ left: rect.left - pad, top: rect.top - pad, width: rect.width + 2 * pad, height: rect.height + 2 * pad }} />}
      <div className="tour-box" style={box} role="dialog" aria-label="Guided tour">
        <div className="step">Step {i + 1} of {steps.length}</div>
        <h3>{s.title}</h3>
        <p className="small">{s.text}</p>
        <div className="row">
          <button className="btn" onClick={close}>Skip tour</button>
          <span className="spacer" />
          {i > 0 && <button className="btn" onClick={() => setI(i - 1)}>Back</button>}
          {i < steps.length - 1
            ? <button className="btn primary" onClick={() => setI(i + 1)}>Next</button>
            : <button className="btn primary" onClick={close}>Done</button>}
        </div>
      </div>
    </>
  )
}

export function useTour(id: string) {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('tour') !== '1') return
    const t = setTimeout(() => setOpen(true), 900)
    return () => clearTimeout(t)
  }, [id])
  useEffect(() => {
    const h = () => setOpen(true)
    window.addEventListener('open-tour', h)
    return () => window.removeEventListener('open-tour', h)
  }, [])
  return { open, close: () => setOpen(false) }
}

export function Legend({ items }: { items: { color: string; label: string; ring?: boolean; square?: boolean; tip?: string }[] }) {
  return (
    <div className="legend">
      {items.map((it) => (
        <span key={it.label} data-tip={it.tip}>
          <i className={`sw ${it.ring ? 'ring' : ''} ${it.square ? 'sq' : ''}`} style={it.ring ? { borderColor: it.color } : { background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

/** Plain-language meanings for the technical words on the site (shown on hover, focus or tap). */
export const GLOSSARY: Record<string, string> = {
  db: 'Decibels (dB): a log scale for echo strength. +10 dB is a 10 times stronger echo, +3 dB about twice as strong.',
  contrast: 'How much brighter the spot is than the sea right around it (signal-to-clutter ratio, in dB). Ships stand out; bright patches of sea do not.',
  probability: "The AI's chance that this is a real ship. It is calibrated: of all spots it rates 80%, about 8 in 10 really are ships.",
  rocauc: 'ROC AUC: the chance that the AI scores a random real ship higher than a random false alarm. 0.5 is guessing, 1 is perfect.',
  prauc: 'Precision-recall AUC: how well the AI keeps precision up while finding more ships. 1 is perfect.',
  ece: 'Expected calibration error: the average gap between the chance the AI states and how often it is right. Lower is better.',
  temperature: 'Temperature scaling: one number fitted on separate passes that softens or sharpens the AI probabilities so they are honest.',
  oddsratio: 'Odds ratio: how much the odds of detection change for each step. 2 doubles the odds, 0.5 halves them, 1 means no effect.',
  ais: 'AIS (Automatic Identification System): radio messages in which ships report who and where they are. Larger ships must send them.',
  sar: 'SAR (synthetic aperture radar): a satellite radar that builds a sharp image from its own echoes. It sees through cloud and at night.',
  interval: '95% interval: the range the true value very likely lies in, given how much the results vary from pass to pass.',
}

/** A technical word with its plain-language meaning on hover, focus or tap. */
export function Term({ k, children }: { k: keyof typeof GLOSSARY | string; children: ReactNode }) {
  return <span className="term" tabIndex={0} data-tip={GLOSSARY[k]}>{children}</span>
}

export function Loading({ what = 'data' }: { what?: string }) {
  return <p className="muted small">Loading {what}…</p>
}

type Q = { error: string | null; slow: boolean; retry: () => void }

/** Loading, slow-server and error states for one or more data requests. Never a dead end. */
export function LoadState({ qs, what }: { qs: Q[]; what: string }) {
  if (qs.some((q) => q.error)) {
    return (
      <div className="load-state" role="alert">
        <p style={{ margin: 0 }}><b>Couldn't load the {what}.</b> The connection may have dropped, or the free server is still waking up.</p>
        <button className="btn primary" style={{ marginTop: '0.6rem' }} onClick={() => qs.forEach((q) => q.error && q.retry())}>Try again</button>
      </div>
    )
  }
  return (
    <div className="load-state" aria-live="polite">
      <p style={{ margin: 0 }}><span className="spinner" aria-hidden="true" /> Loading the {what}…</p>
      {qs.some((q) => q.slow) && (
        <p className="small muted" style={{ margin: '0.4rem 0 0' }}>This demo runs on a free server that sleeps when nobody is using it. Waking it up takes about 20 to 30 seconds, and only happens once.</p>
      )}
    </div>
  )
}

/** The season shown on a page, kept in the address (?season=) so a link keeps the choice. */
export function useSeason(def: Season = 'both'): [Season, (s: Season) => void] {
  const [sp, setSp] = useSearchParams()
  const v = sp.get('season')
  const season: Season = v === 'summer' || v === 'winter' || v === 'both' ? v : def
  const set = (s: Season) => {
    const n = new URLSearchParams(sp)
    if (s === def) n.delete('season')
    else n.set('season', s)
    setSp(n, { replace: true, preventScrollReset: true })
  }
  return [season, set]
}

/** Summer, winter or both: a small switch for every page that shows results by season. */
export function SeasonSwitch({ value, onChange, options = ['summer', 'winter', 'both'], label = 'Season' }:
  { value: Season; onChange: (s: Season) => void; options?: Season[]; label?: string }) {
  return (
    <div className="season-switch" role="radiogroup" aria-label={label}>
      <span className="tiny muted">{label}</span>
      {options.map((o) => (
        <button key={o} role="radio" aria-checked={value === o} className={`chip ${value === o ? 'on' : ''}`} onClick={() => onChange(o)}>{SEASON_LABEL[o]}</button>
      ))}
    </div>
  )
}
