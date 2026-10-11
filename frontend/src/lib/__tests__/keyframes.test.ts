import { describe, expect, it } from 'vitest'
import { findMissingKeyframes } from '../keyframes'

describe('findMissingKeyframes', () => {
  it('finds a name a module renamed away from its keyframes', () => {
    // What the blueprint's bug looks like once built: the module's use was
    // scoped, the global keyframes were not.
    const css =
      '@keyframes app-pop-in{from{opacity:0}}' +
      '._panel_x1{animation:_app-pop-in_x1 .22s cubic-bezier(.34,1.28,.64,1) both}'
    expect(findMissingKeyframes(css)).toEqual(['_app-pop-in_x1'])
  })

  it('reads the names inside the --anim-* tokens and passes when they exist', () => {
    const css =
      ':root{--anim-rise-in:app-rise-in var(--motion-default) var(--ease-standard) both;' +
      '--anim-spin:app-spin .7s linear infinite}' +
      '@keyframes app-rise-in{from{opacity:0}}@keyframes app-spin{to{rotate:1turn}}' +
      '._card_a{animation:var(--anim-rise-in)}._spinner_b{animation:var(--anim-spin)}'
    expect(findMissingKeyframes(css)).toEqual([])
  })

  it('reads every animation in a list, and animation-name', () => {
    const css =
      '@keyframes a{}.x{animation:a 1s ease-in-out,b 2s steps(4,end) infinite alternate}' +
      '.y{animation-name:c}'
    expect(findMissingKeyframes(css)).toEqual(['b', 'c'])
  })

  it('ignores keywords, other longhands and a reset to none', () => {
    const css =
      '.x{animation:none!important;animation-duration:1ms;animation-timing-function:ease}' +
      '.y{animation:1s ease-out both}'
    expect(findMissingKeyframes(css)).toEqual([])
  })
})
