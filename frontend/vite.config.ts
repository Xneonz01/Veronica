import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
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
  },
})
