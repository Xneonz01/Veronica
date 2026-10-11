// Small display helpers for the Memory page.

export function relativeTime(iso: string | null): string | null {
  if (!iso) return null
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  // "26 Jul", with the year only when it isn't this one.
  const date = new Date(iso)
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: sameYear ? undefined : 'numeric' })
}

export function mimeLabel(mime: string): string {
  if (mime.includes('pdf')) return 'PDF'
  if (mime.includes('markdown')) return 'Markdown'
  return 'Text'
}
