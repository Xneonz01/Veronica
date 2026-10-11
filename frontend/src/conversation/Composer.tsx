// Where you write to her. Enter sends and Shift+Enter starts a new line; while
// she answers, Send becomes Stop in the same place and Escape stops too. Files
// can be attached, pasted or dropped anywhere on the page.

import { ArrowUp, Paperclip, Square } from 'lucide-react'
import { useLayoutEffect, useRef, type ClipboardEvent, type KeyboardEvent, type RefObject } from 'react'
import type { Attachment } from '../chat/useAttachments'
import type { LaneState } from '../chat/types'
import { IconButton } from '../ui/Button'
import { AttachmentChips } from './AttachmentChips'
import styles from './Composer.module.css'

export type ComposerProps = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onStop: () => void
  /** She is answering: Send becomes Stop. */
  busy: boolean
  /** Anything but open, and nothing can be sent; writing carries on regardless. */
  lane: LaneState
  attachments: readonly Attachment[]
  uploading: readonly string[]
  onAttach: (files: File[]) => void
  onRemoveAttachment: (id: number) => void
  fieldRef: RefObject<HTMLTextAreaElement | null>
}

// The first connection and a lost one read differently: only one of them is news.
const PLACEHOLDER: Partial<Record<LaneState, string>> = {
  connecting: 'Connecting to Veronica…',
  offline: 'Reconnecting…',
  unavailable: 'Chat is paused',
}

export function Composer(props: ComposerProps) {
  const { value, busy, lane, fieldRef } = props
  // Only what needs the backend waits for it. Typing never does: a dropped
  // connection mid-sentence must not take the words or the focus away.
  const disabled = lane !== 'open'
  const picker = useRef<HTMLInputElement>(null)
  const canSend = !busy && !disabled && value.trim().length > 0

  // Grows with what you write, up to a point, then scrolls: it never crowds out
  // the conversation.
  useLayoutEffect(() => {
    const field = fieldRef.current
    if (!field) return
    field.style.height = 'auto'
    // The ceiling is the stylesheet's max-height, read rather than repeated here.
    const ceiling = parseFloat(getComputedStyle(field).maxHeight) || Infinity
    field.style.height = `${Math.min(field.scrollHeight, ceiling)}px`
  }, [value, fieldRef])

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter while an input method is composing a character picks the character.
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      if (canSend) props.onSend()
    } else if (event.key === 'Escape' && busy) {
      event.preventDefault()
      props.onStop()
    }
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.files)
    if (files.length === 0) return
    event.preventDefault()
    // A pasted image has no name; give it one so its chip isn't blank.
    props.onAttach(files.map((file) => (file.name ? file : new File([file], 'pasted-image.png', { type: file.type }))))
  }

  return (
    <div className={styles.composer} data-disabled={disabled || undefined}>
      <AttachmentChips items={props.attachments} uploading={props.uploading} onRemove={props.onRemoveAttachment} />
      <textarea
        ref={fieldRef}
        className={styles.field}
        rows={1}
        value={value}
        aria-label="Message Veronica"
        placeholder={PLACEHOLDER[lane] ?? (busy ? 'Veronica is answering…' : 'Message Veronica')}
        onChange={(event) => props.onChange(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
      />
      <div className={styles.toolbar}>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            if (event.target.files?.length) props.onAttach(Array.from(event.target.files))
            event.target.value = '' // so the same file can be picked twice running
          }}
        />
        <IconButton
          label="Attach files"
          icon={<Paperclip size={18} aria-hidden="true" />}
          disabled={disabled}
          onClick={() => picker.current?.click()}
        />
        <span className={styles.spacer} />
        {/* Keyed apart: one reused button would take the second click of a double-
            click on Send as Stop. */}
        {busy ? (
          <IconButton
            key="stop"
            round
            variant="secondary"
            label="Stop answering"
            aria-keyshortcuts="Escape"
            icon={<Square size={13} fill="currentColor" aria-hidden="true" />}
            onClick={props.onStop}
          />
        ) : (
          <IconButton
            key="send"
            round
            variant="primary"
            label="Send"
            icon={<ArrowUp size={18} strokeWidth={2.4} aria-hidden="true" />}
            disabled={!canSend}
            onClick={props.onSend}
          />
        )}
      </div>
    </div>
  )
}
