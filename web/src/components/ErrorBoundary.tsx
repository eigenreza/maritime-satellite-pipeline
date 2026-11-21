import { Component, type ErrorInfo, type ReactNode } from 'react'

/**
 * Catches a crash in one page and shows a way out instead of a blank screen. The usual cause on a
 * live site is a stale page after a new version went up (an old script file is gone): a reload fixes it.
 */
/** Mounted with key={path}, so moving to another page starts with a clean slate. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('page error', error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <main className="page">
        <div className="load-state" role="alert">
          <p style={{ margin: 0 }}><b>This page hit a problem.</b> Most often a newer version of the site went live while the page was open. Reloading fixes it.</p>
          <div className="row" style={{ marginTop: '0.6rem', gap: '0.5rem' }}>
            <button className="btn primary" onClick={() => window.location.reload()}>Reload the page</button>
            <a className="btn" href="/">Go to the start page</a>
          </div>
        </div>
      </main>
    )
  }
}
