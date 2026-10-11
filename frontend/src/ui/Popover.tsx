// A panel anchored to a control: a menu, a picker, a short form. It lives in the
// browser's top layer (the popover API) instead of a portal. The top layer
// escapes every clipping ancestor, and the panel stays a descendant of wherever
// it is written, which matters inside a Sheet: a modal dialog makes everything
// outside itself inert, a portal to <body> included.
//
// The owner holds `open`. The panel asks to close on Escape and on a press
// outside it and its anchor (the anchor's own click is left to toggle it).

import { useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react'
import { cx } from './cx'
import styles from './Popover.module.css'
import { place, type Placement } from './popoverPlacement'

export type PopoverProps = {
  anchor: RefObject<HTMLElement | null>
  open: boolean
  onClose: () => void
  placement?: Placement
  /** The panel's accessible name. */
  label: string
  children: ReactNode
  className?: string
}

export function Popover(props: PopoverProps) {
  return props.open ? <OpenPopover {...props} /> : null
}

function OpenPopover({
  anchor,
  onClose,
  placement = 'bottom-start',
  label,
  children,
  className,
}: PopoverProps) {
  const panel = useRef<HTMLDivElement>(null)
  // The newest onClose, without re-subscribing every listener on each render.
  const close = useRef(onClose)
  useLayoutEffect(() => {
    close.current = onClose
  })

  useLayoutEffect(() => {
    const element = panel.current
    const trigger = anchor.current
    if (!element) return
    element.showPopover()

    // Measured and written before the first paint, so the panel never shows in
    // the wrong place for a frame. The panel's size is its layout size: the
    // bounding box would include the entrance's scale and come out 3% short.
    const position = () => {
      if (!trigger) return
      const spot = place(
        trigger.getBoundingClientRect(),
        { width: element.offsetWidth, height: element.offsetHeight },
        { width: window.innerWidth, height: window.innerHeight },
        placement,
      )
      element.style.top = `${spot.top}px`
      element.style.left = `${spot.left}px`
      element.dataset.side = spot.side
      element.dataset.align = spot.align
    }
    position()

    let frame = 0
    const follow = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(position)
    }
    const pressOutside = (event: PointerEvent) => {
      const target = event.target instanceof Node ? event.target : null
      if (element.contains(target) || trigger?.contains(target)) return
      close.current()
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // Taken here, so the same key doesn't also close a sheet underneath.
      event.preventDefault()
      event.stopPropagation()
      close.current()
    }
    window.addEventListener('resize', follow)
    // Capture, to hear a scroll in any container the anchor sits in.
    window.addEventListener('scroll', follow, true)
    document.addEventListener('pointerdown', pressOutside, true)
    document.addEventListener('keydown', escape, true)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', follow)
      window.removeEventListener('scroll', follow, true)
      document.removeEventListener('pointerdown', pressOutside, true)
      document.removeEventListener('keydown', escape, true)
      // Focus that was inside the panel, or was lost with it, goes back to the
      // control that opened it. Focus the person moved elsewhere stays there.
      const active = document.activeElement
      if (active === null || active === document.body || element.contains(active)) trigger?.focus()
    }
  }, [anchor, placement])

  return (
    <div
      ref={panel}
      popover="manual"
      role="dialog"
      aria-label={label}
      className={cx(styles.panel, className)}
    >
      {children}
    </div>
  )
}
