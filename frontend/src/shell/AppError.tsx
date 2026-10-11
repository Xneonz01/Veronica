// What you see when a screen fails to draw, instead of React Router's unstyled page
// with the raw message (and a stack trace in development). Four pieces, because
// they catch different things:
//   PageError        one page threw: it is replaced inside the shell, which keeps
//                    running around it (a live call included).
//   RouteError       the router's errorElement: an address that leads nowhere, or
//                    the shell itself throwing.
//   AppErrorScreen   the whole-window failure, for both of the last two.
//   AppErrorBoundary a class boundary for what throws outside the router.
// The error goes to the console for whoever is debugging, never onto the screen.

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { isRouteErrorResponse, useNavigate, useRouteError } from 'react-router-dom'
import { Button } from '../ui/Button'
import { Mark } from '../ui/Mark'
import styles from './AppError.module.css'

export function AppErrorScreen() {
  return (
    <main className={styles.screen}>
      <div className={styles.message} role="alert">
        <Mark size={40} className={styles.mark} />
        <h1 className={styles.title}>This screen stopped working</h1>
        <p className={styles.body}>Veronica is still running; only this view failed. Reloading usually brings it back.</p>
        <Button variant="primary" onClick={() => window.location.reload()}>
          Reload
        </Button>
      </div>
    </main>
  )
}

/** A page that failed, in its own place: the sidebar, a call and everything else go on. */
export function PageError() {
  return (
    <section className={styles.page}>
      <div className={styles.message} role="alert">
        <Mark size={40} className={styles.mark} />
        <h1 className={styles.title}>This page stopped working</h1>
        <p className={styles.body}>
          Everything else is still running. Open another page from the sidebar, or reload the window to bring this one back.
        </p>
        <Button variant="secondary" onClick={() => window.location.reload()}>
          Reload the window
        </Button>
      </div>
    </section>
  )
}

export function RouteError() {
  const error = useRouteError()
  const navigate = useNavigate()
  if (!isRouteErrorResponse(error) || error.status !== 404) return <AppErrorScreen />
  return (
    <main className={styles.screen}>
      <div className={styles.message}>
        <Mark size={40} className={styles.mark} />
        <h1 className={styles.title}>There's nothing here</h1>
        <p className={styles.body}>That address doesn't lead to a page in Veronica.</p>
        <Button variant="primary" onClick={() => void navigate('/')}>
          Go to chat
        </Button>
      </div>
    </main>
  )
}

interface Props {
  children?: ReactNode
}

interface State {
  failed: boolean
}

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled UI error', error, info.componentStack)
  }

  render(): ReactNode {
    return this.state.failed ? <AppErrorScreen /> : this.props.children
  }
}
