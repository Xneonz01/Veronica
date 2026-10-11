// A timer that a person can hold: it pauses while they point at or focus the
// thing it would dismiss, and resumes with the time that was left. Pointer and
// focus are held separately, so moving the pointer away from a toast whose button
// still has the keyboard doesn't restart the clock.

import { useEffect, useLayoutEffect, useRef, useState } from 'react'

export function useExpiry(durationMs: number, onExpire: () => void) {
  const [pointed, setPointed] = useState(false)
  const [focused, setFocused] = useState(false)
  const paused = pointed || focused
  const remaining = useRef(durationMs)
  const expire = useRef(onExpire)
  useLayoutEffect(() => {
    expire.current = onExpire
  })

  useEffect(() => {
    if (paused) return
    const deadline = performance.now() + remaining.current
    const timer = window.setTimeout(() => expire.current(), remaining.current)
    return () => {
      window.clearTimeout(timer)
      remaining.current = Math.max(0, deadline - performance.now())
    }
  }, [paused])

  return {
    paused,
    holders: {
      onPointerEnter: () => setPointed(true),
      onPointerLeave: () => setPointed(false),
      onFocus: () => setFocused(true),
      onBlur: () => setFocused(false),
    },
  }
}
