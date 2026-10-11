import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Inter with its weight and optical-size axes, self-hosted: no font CDN.
import '@fontsource-variable/inter/opsz.css'
import './index.css'
import App from './App.tsx'
import { AppErrorBoundary } from './shell/AppError'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </StrictMode>,
)
