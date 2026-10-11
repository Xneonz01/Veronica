// A size of memory in the words Windows uses for it, for the check of this computer in
// Settings and a call the graphics card has no room for.

const TENTHS = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const WHOLE = new Intl.NumberFormat('en-US')
// Between a figure and its unit, a space that doesn't break: "16.0 GB" never splits.
export const UNIT_SPACE = ' '

export type Rounding = 'nearest' | 'down' | 'up'
const ROUND: Record<Rounding, (value: number) => number> = { nearest: Math.round, down: Math.floor, up: Math.ceil }

/** Whole MiB as the size Windows would show: GB with one decimal, MB below a GB. The
    backend rounds MB already, a need up and what is free down. */
export function memorySize(mib: string | number | undefined, rounding: Rounding = 'nearest'): string | null {
  if (typeof mib !== 'number' || !Number.isFinite(mib) || mib < 0) return null
  if (mib < 1024) return `${WHOLE.format(mib)}${UNIT_SPACE}MB`
  // Tenths of a GB, exact: dividing a whole number by 1,024 loses nothing in binary.
  return `${TENTHS.format(ROUND[rounding]((mib * 10) / 1024) / 10)}${UNIT_SPACE}GB`
}
