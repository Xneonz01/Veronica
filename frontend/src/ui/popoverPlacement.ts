// Where an anchored panel goes, as a pure function of the anchor's box, the
// panel's size and the window's size, so the rule can be tested without a
// layout engine: below or above the anchor, lined up with one of its edges,
// flipped when the preferred side is short of room, and never nearer than EDGE
// to the window's edge.

export type Side = 'top' | 'bottom'
export type Align = 'start' | 'end'
export type Placement = 'top-start' | 'top-end' | 'bottom-start' | 'bottom-end'

export type Box = { top: number; left: number; bottom: number; right: number }
export type Size = { width: number; height: number }
export type Placed = { top: number; left: number; side: Side; align: Align }

/** Between the anchor and the panel. */
export const GAP = 8
/** Between the panel and the window's edge. */
export const EDGE = 8

export function place(anchor: Box, panel: Size, viewport: Size, placement: Placement): Placed {
  const preferred: Side = placement.startsWith('top') ? 'top' : 'bottom'
  const align: Align = placement.endsWith('end') ? 'end' : 'start'
  const room: Record<Side, number> = {
    top: anchor.top - GAP - EDGE,
    bottom: viewport.height - anchor.bottom - GAP - EDGE,
  }
  const other: Side = preferred === 'top' ? 'bottom' : 'top'
  // Flip only when it helps: a panel too tall for either side stays where it
  // was asked to be and is held inside the window instead.
  const side = room[preferred] >= panel.height || room[preferred] >= room[other] ? preferred : other

  const top = side === 'bottom' ? anchor.bottom + GAP : anchor.top - GAP - panel.height
  const left = align === 'start' ? anchor.left : anchor.right - panel.width
  return {
    top: clamp(top, EDGE, viewport.height - panel.height - EDGE),
    left: clamp(left, EDGE, viewport.width - panel.width - EDGE),
    side,
    align,
  }
}

/** A panel larger than the window pins to the near edge rather than leaving it. */
function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max))
}
