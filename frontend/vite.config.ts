import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { CONTENT_SECURITY_POLICY } from './src/lib/csp.ts'

/** The page's policy (src/lib/csp.ts), first in the built page's head, before anything
 *  it governs. Not in development: the dev server's hot reload runs inline code. */
function contentSecurityPolicy(): Plugin {
  return {
    name: 'veronica-content-security-policy',
    apply: 'build',
    transformIndexHtml: () => [
      { tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: CONTENT_SECURITY_POLICY }, injectTo: 'head-prepend' },
    ],
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), contentSecurityPolicy()],
  // Tauri expects a fixed dev-server port matching tauri.conf.json devUrl.
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: {
    // The desktop app runs in WebView2, an evergreen Chromium. Aimed at older
    // browsers, the CSS minifier rewrites every light-dark() into fallback
    // variables that resolve where a token is declared, so a theme set on an
    // inner element (the design page's islands) would be ignored in production.
    // light-dark() needs Chromium 123.
    cssTarget: 'chrome123',
    // Every asset is a file of its own: the policy loads fonts and images from this
    // app only, and an asset inlined as a data: address would be refused.
    assetsInlineLimit: 0,
  },
})
