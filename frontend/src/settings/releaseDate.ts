// A release's date as the patch notes show it.

/** "2026-09-20" as "20 Sep 2026"; anything else as it came. */
export function releaseDate(date: string): string {
  const parsed = /^\d{4}-\d{2}-\d{2}$/.test(date) ? new Date(`${date}T00:00:00`) : null
  if (!parsed || Number.isNaN(parsed.getTime())) return date
  return parsed.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}
