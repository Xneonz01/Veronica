// Veronica's mark: a ring in the ink, her orb in emerald, and two arcs of voice
// leaving it. Drawn from tokens so it follows the theme; the arcs are weighted
// to survive at 17px in the answer gutter as well as at 40px on their own.

type MarkProps = {
  size?: number
  className?: string
}

// Both arcs are centred on the orb (13.4, 17.6): r 8.6 from -72° to 2°, and
// r 12.2 from -68° to 0°, which clears the ring's inner edge by about a unit.
const NEAR_ARC = 'M16.06 9.42A8.6 8.6 0 0 1 21.99 17.9'
const FAR_ARC = 'M17.97 6.29A12.2 12.2 0 0 1 25.6 17.6'

export function Mark({ size = 28, className }: MarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <circle
        cx="16"
        cy="16"
        r="12.4"
        fill="none"
        strokeWidth="1.5"
        stroke="color-mix(in oklab, var(--foreground) 24%, transparent)"
      />
      <circle cx="13.4" cy="17.6" r="5.6" fill="var(--assistant)" />
      <path
        d={NEAR_ARC}
        fill="none"
        stroke="var(--assistant)"
        strokeWidth="1.9"
        strokeLinecap="round"
        opacity="0.85"
      />
      <path
        d={FAR_ARC}
        fill="none"
        stroke="var(--assistant)"
        strokeWidth="1.7"
        strokeLinecap="round"
        opacity="0.5"
      />
    </svg>
  )
}
