import { lazy, Suspense, useEffect } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Footer, Header, Loading, TooltipLayer } from './components/ui'
import Start from './pages/Start'

const SeaMap = lazy(() => import('./pages/SeaMap'))
const Radar = lazy(() => import('./pages/Radar'))
const AI = lazy(() => import('./pages/AI'))
const Game = lazy(() => import('./pages/Game'))
const Clouds = lazy(() => import('./pages/Clouds'))
const Ops = lazy(() => import('./pages/Ops'))
const Catalogue = lazy(() => import('./pages/Catalogue'))
const Built = lazy(() => import('./pages/Built'))

const TITLES: Record<string, string> = {
  '/': 'Start here', '/map': 'Sea map', '/radar': 'What radar sees', '/ai': 'The AI', '/game': 'You vs the AI',
  '/clouds': 'Clouds vs radar', '/ops': 'Pipeline and operations', '/catalogue': 'Data catalogue', '/built': 'How it is built',
}

export default function App() {
  const loc = useLocation()
  useEffect(() => {
    document.title = `${TITLES[loc.pathname] ?? 'Page'} · Ships, radar and sea state off Norway`
    window.scrollTo(0, 0)
  }, [loc.pathname])
  return (
    <div className="app">
      <Header onHelp={() => window.dispatchEvent(new Event('open-tour'))} />
      <ErrorBoundary key={loc.pathname}>
      <Suspense fallback={<main className="page"><Loading what="page" /></main>}>
        <Routes>
          <Route path="/" element={<Start />} />
          <Route path="/map" element={<SeaMap />} />
          <Route path="/radar" element={<Radar />} />
          <Route path="/ai" element={<AI />} />
          <Route path="/game" element={<Game />} />
          <Route path="/clouds" element={<Clouds />} />
          <Route path="/ops" element={<Ops />} />
          <Route path="/catalogue" element={<Catalogue />} />
          <Route path="/built" element={<Built />} />
          <Route path="*" element={<Start />} />
        </Routes>
      </Suspense>
      </ErrorBoundary>
      <Footer />
      <TooltipLayer />
    </div>
  )
}
