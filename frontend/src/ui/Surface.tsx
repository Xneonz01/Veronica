// Surfaces: the six materials every card is built from, so the materials can't
// drift between components. Flat is the default and most of the product; glass
// is for chrome and results; emissive is Veronica present, once per screen.

import { createElement, type HTMLAttributes, type PointerEvent, type ReactNode } from 'react'
import { cx } from './cx'
import styles from './Surface.module.css'

export type Material = 'flat' | 'subtle' | 'glass' | 'elevated' | 'interactive' | 'emissive'
export type Radius = 'sm' | 'md' | 'lg' | 'xl' | '2xl'

const RADIUS: Record<Radius, string> = {
  sm: styles.rsm,
  md: styles.rmd,
  lg: styles.rlg,
  xl: styles.rxl,
  '2xl': styles.r2xl,
}

export type SurfaceProps = HTMLAttributes<HTMLElement> & {
  material?: Material
  radius?: Radius
  as?: 'div' | 'section' | 'article' | 'aside' | 'header' | 'footer' | 'li'
  children?: ReactNode
}

/** Writes the pointer's position onto the element itself: moving the mouse over a
    card must not re-render it. */
function followPointer(event: PointerEvent<HTMLElement>) {
  const target = event.currentTarget
  const box = target.getBoundingClientRect()
  target.style.setProperty('--sheen-x', `${((event.clientX - box.left) / box.width) * 100}%`)
  target.style.setProperty('--sheen-y', `${((event.clientY - box.top) / box.height) * 100}%`)
}

export function Surface({
  material = 'flat',
  radius = 'lg',
  as = 'div',
  className,
  children,
  onPointerMove,
  ...rest
}: SurfaceProps) {
  const sheen = material === 'interactive' || material === 'emissive'
  return createElement(
    as,
    {
      ...rest,
      className: cx(styles.surface, styles[material], RADIUS[radius], className),
      onPointerMove: sheen
        ? (event: PointerEvent<HTMLElement>) => {
            followPointer(event)
            onPointerMove?.(event)
          }
        : onPointerMove,
    },
    children,
  )
}
