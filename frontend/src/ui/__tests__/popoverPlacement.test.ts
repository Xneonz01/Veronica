import { describe, expect, it } from 'vitest'
import { EDGE, GAP, place, type Box } from '../popoverPlacement'

const viewport = { width: 1000, height: 800 }
const panel = { width: 240, height: 200 }

function box(left: number, top: number, width = 100, height = 32): Box {
  return { left, top, right: left + width, bottom: top + height }
}

describe('place', () => {
  it('sits below its anchor, lined up with the start edge', () => {
    expect(place(box(100, 100), panel, viewport, 'bottom-start')).toEqual({
      top: 132 + GAP,
      left: 100,
      side: 'bottom',
      align: 'start',
    })
  })

  it('lines up with the end edge', () => {
    const spot = place(box(500, 100), panel, viewport, 'bottom-end')
    expect(spot.left).toBe(600 - panel.width)
    expect(spot.align).toBe('end')
  })

  it('flips above when there is no room below', () => {
    const spot = place(box(100, 700), panel, viewport, 'bottom-start')
    expect(spot).toMatchObject({ side: 'top', top: 700 - GAP - panel.height })
  })

  it('flips below when a top placement has no room above', () => {
    expect(place(box(100, 50), panel, viewport, 'top-start').side).toBe('bottom')
  })

  it('stays on its side when neither fits and it still has more room', () => {
    const tall = { width: 240, height: 700 }
    const spot = place(box(100, 500), tall, viewport, 'top-start')
    expect(spot.side).toBe('top')
    // Held inside the window rather than running off the top.
    expect(spot.top).toBe(EDGE)
  })

  it('keeps its distance from the window edge', () => {
    const spot = place(box(900, 100), panel, viewport, 'bottom-start')
    expect(spot.left).toBe(viewport.width - panel.width - EDGE)
  })

  it('pins a panel wider than the window to the near edge', () => {
    const wide = { width: 1200, height: 100 }
    expect(place(box(100, 100), wide, viewport, 'bottom-end').left).toBe(EDGE)
  })
})
