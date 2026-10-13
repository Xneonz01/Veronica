// The stored theme goes on before the first paint, or a dark-theme window would flash
// light while the app loads. Mirrors applyTheme in src/lib/theme.ts. A file rather than
// a script in the page: the page's Content-Security-Policy runs no inline code.
try {
  var theme = localStorage.getItem('veronica.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme
} catch {}
