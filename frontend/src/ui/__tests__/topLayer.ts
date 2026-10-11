// jsdom has <dialog> and the popover attribute but none of the top-layer methods.
// These stand-ins do what the components rely on: showModal and close toggle
// the `open` attribute, and close fires `close` the way a browser does.

import { vi } from 'vitest'

export function stubTopLayer() {
  const showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '')
  })
  const close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open')
    this.dispatchEvent(new Event('close'))
  })
  const showPopover = vi.fn()
  const hidePopover = vi.fn()
  HTMLDialogElement.prototype.showModal = showModal
  HTMLDialogElement.prototype.close = close
  HTMLElement.prototype.showPopover = showPopover
  HTMLElement.prototype.hidePopover = hidePopover
  return { showModal, close, showPopover, hidePopover }
}
