// Her personality, in your words. Nothing applies until you save; then chat and
// calls use it at once. The fixed instructions around it are shown read-only, so
// nobody tries to overrule them from the personality text.
//
// Until you type, the editor shows the saved text and follows it live. Leaving with
// unsaved changes asks first, whether that is another page or another tab here.

import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useBlocker } from 'react-router-dom'
import { messageFor } from '../lib/api'
import { matches, SHORTCUTS } from '../lib/shortcuts'
import { Button } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { Notice, Skeleton } from '../ui/Feedback'
import { Disclosure } from '../ui/Disclosure'
import { TextArea } from '../ui/Field'
import { cx } from '../ui/cx'
import { Group } from './Group'
import styles from './PersonalitySettings.module.css'
import { usePersona } from './usePersona'

const SAVED_NOTE_MS = 3000

export function PersonalitySettings() {
  const { persona, loadError, retry, save } = usePersona()
  // null while you are not editing: the editor then shows the saved text.
  const [draft, setDraft] = useState<string | null>(null)
  // The saved text when you started editing, to notice a save made elsewhere.
  const [editedFrom, setEditedFrom] = useState('')
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const field = useRef<HTMLTextAreaElement>(null)

  const dirty = persona !== null && draft !== null && draft !== persona.personality
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search),
  )

  useEffect(() => {
    if (!saved) return
    const timer = window.setTimeout(() => setSaved(false), SAVED_NOTE_MS)
    return () => window.clearTimeout(timer)
  }, [saved])

  if (persona === null) {
    return (
      <Group title="Her personality" bare={loadError !== null}>
        {loadError ? (
          <Notice tone="critical" live action={<Button size="sm" onClick={() => void retry()}>Try again</Button>}>
            {loadError}
          </Notice>
        ) : (
          <div className={styles.loading} role="status">
            <span className="visually-hidden">Loading her personality</span>
            <Skeleton height={180} radius={12} />
            <Skeleton width={180} height={32} radius={8} />
          </div>
        )}
      </Group>
    )
  }

  const text = draft ?? persona.personality
  const over = text.length > persona.maxChars
  // Not while saving: the backend announces the new text before it answers the save,
  // and your own save would read for a moment as someone else's.
  const changedElsewhere = dirty && !saving && editedFrom !== persona.personality

  function edit(next: string) {
    if (draft === null) setEditedFrom(persona?.personality ?? '')
    setDraft(next)
    setSaved(false)
  }

  function discard() {
    setDraft(null)
    setFailed(null)
    field.current?.focus() // the button that was pressed has just gone
  }

  async function commit() {
    if (!dirty || over || saving) return
    const sent = text
    setSaving(true)
    setFailed(null)
    try {
      const stored = await save(sent)
      // What was typed while it saved is kept, as the next thing to save.
      setDraft((current) => (current === sent ? null : current))
      setEditedFrom(stored.personality)
      setSaved(true)
    } catch (error) {
      setFailed(messageFor(error, "Couldn't save that. Try again in a moment."))
    } finally {
      setSaving(false)
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!matches(event.nativeEvent, SHORTCUTS.save)) return
    event.preventDefault()
    void commit()
  }

  return (
    <>
      <Group
        title="Her personality"
        lead="Her character, in your words. Chat and calls use it the moment you save. It never changes what she is allowed to do."
      >
        <div className={styles.editor}>
          {changedElsewhere && (
            <Notice
              tone="attention"
              action={
                <Button size="sm" onClick={discard}>
                  Show the new one
                </Button>
              }
            >
              Her personality was saved somewhere else while you were editing. Saving yours replaces it.
            </Notice>
          )}
          <TextArea
            ref={field}
            label="Personality"
            hideLabel
            rows={9}
            value={text}
            spellCheck
            onChange={(event) => edit(event.target.value)}
            onKeyDown={onKeyDown}
            // Worded the same whatever the length, so it is announced once as the
            // limit is crossed, not at every keystroke past it. The count says by how much.
            error={over ? "That's longer than she can take. Shorten it to save." : undefined}
            hint={text.trim() === '' ? 'Saved empty, she goes back to her original personality.' : undefined}
          />
          <div className={styles.actions}>
            <Button variant="primary" onClick={() => void commit()} disabled={!dirty || over} loading={saving}>
              Save
            </Button>
            {dirty && (
              <Button variant="ghost" onClick={discard} disabled={saving}>
                Discard changes
              </Button>
            )}
            <Button variant="quiet" onClick={() => edit(persona.original)} disabled={saving || text === persona.original}>
              Start from the original
            </Button>
            <Count length={text.length} max={persona.maxChars} />
          </div>
          <p className={styles.saved} role="status">
            {saved ? "Saved. She's using it now." : ''}
          </p>
          {failed && (
            <Notice tone="critical" live>
              {failed}
            </Notice>
          )}
          <Disclosure className={styles.scaffold} summary={<span className={styles.summary}>What always applies, whatever you write</span>}>
            <pre className={styles.prompt}>{persona.scaffold}</pre>
          </Disclosure>
        </div>
      </Group>

      <Confirm
        open={blocker.state === 'blocked'}
        title="Leave without saving?"
        confirmLabel="Leave"
        cancelLabel="Keep editing"
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      >
        Your changes to her personality aren't saved yet.
      </Confirm>
    </>
  )
}

/** Characters used against the limit: amber from nine tenths, red over it. */
function Count({ length, max }: { length: number; max: number }) {
  const tone = length > max ? styles.over : length >= max * 0.9 ? styles.near : undefined
  return (
    <span className={cx(styles.count, 'num', tone)}>
      {length.toLocaleString()} / {max.toLocaleString()}
    </span>
  )
}
