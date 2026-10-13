// The conversation page. The session itself (socket, messages, which
// conversation) lives in the shell's workspace, so the sidebar can switch
// conversations and a reply keeps streaming while you are on another page.
// Permission requests float over every page from the shell, just above here.

import { Paperclip } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type DragEvent } from 'react'
import { useAttachments } from '../chat/useAttachments'
import type { LaneState } from '../chat/types'
import { chatProblem } from '../conversation/chatProblems'
import { ChatHeader } from '../conversation/ChatHeader'
import { Composer } from '../conversation/Composer'
import { Transcript } from '../conversation/Transcript'
import { Welcome } from '../conversation/Welcome'
import { useNotifications } from '../notify/useNotifications'
import { useWebSettings } from '../settings/useWebSettings'
import { Notice, Skeleton } from '../ui/Feedback'
import { Button } from '../ui/Button'
import { useChatSession, useWorkspace } from '../workspace/context'
import { titleOf } from '../workspace/recents'
import styles from './Chat.module.css'

const FINEPRINT: Record<LaneState, string> = {
  open: 'Veronica runs on this computer. Nothing you say to her leaves it.',
  connecting: 'Connecting to Veronica…',
  offline: 'Reconnecting to Veronica. Keep writing; you can send once she is back.',
  unavailable: "Chat is paused while her memory can't be opened. The notice above says what to do.",
}
// With web search on, the promise is narrower, as her persona's is (ADR 0003); while
// the setting is still loading, only what is true either way.
const WEB_ON = 'Veronica runs on this computer. What she looks up on the web goes to your search service.'
const WEB_UNKNOWN = 'Veronica runs on this computer.'

export function Chat() {
  const chat = useChatSession()
  const { conversations, projects, activeProjectId } = useWorkspace()
  const attachments = useAttachments(chat.conversationId)
  const { history, undo } = useNotifications()
  const web = useWebSettings().settings
  const field = useRef<HTMLTextAreaElement | null>(null)
  const [draft, setDraft] = useState('')
  const [stoppedAfter, setStoppedAfter] = useState<number | null>(null)
  const [dragging, setDragging] = useState(false)

  // Memory written during the answer on screen is receipted under it, a line for each
  // write. Read from the history, not the latest toast: two writes landing in one
  // render showed only the second. `since` is the newest notification's number when
  // the question was asked or the conversation opened.
  const newest = history[0]?.createdAt ?? 0
  const newestRef = useRef(newest)
  newestRef.current = newest
  const [since, setSince] = useState(newest)
  const [undone, setUndone] = useState<string[]>([])
  const receipts = history
    .filter((item) => item.toasted && item.createdAt > since && !undone.includes(item.id))
    .reverse()

  // Receipts and a stop belong to the conversation they happened in; a new one
  // starts clean, with the keyboard in the composer.
  useEffect(() => {
    setSince(newestRef.current)
    setStoppedAfter(null)
    field.current?.focus()
  }, [chat.conversationId])

  // A message sent back unread goes back in the box once, over nothing typed since.
  const { returned, clearReturned } = chat
  useEffect(() => {
    if (!returned) return
    setDraft((current) => (current.trim() ? current : returned.text))
    field.current?.focus()
    clearReturned()
  }, [returned, clearReturned])

  const lastQuestion = [...chat.messages].reverse().find((message) => message.role === 'user') ?? null
  const summary = conversations.find((item) => item.id === chat.conversationId) ?? null
  const projectId = summary ? summary.project_id : activeProjectId
  const project = projects.find((item) => item.id === projectId)?.name ?? null
  // The welcome is for a new conversation only: a stored one, even an empty one or
  // one that failed to open, gets its header and its transcript.
  const empty = chat.messages.length === 0 && chat.state === 'idle' && chat.conversationId === null
  const loading = chat.opening
  const busy = chat.state !== 'idle'
  const problem = chatProblem(chat.errorKind, chat.errorDetail)

  // The first message moves the composer from the welcome to the dock, which is a
  // new field: the keyboard follows it rather than waiting for the conversation id.
  useLayoutEffect(() => {
    if (!empty) field.current?.focus()
  }, [empty])

  function send() {
    const text = draft.trim()
    if (!text || busy) return
    if (chat.send(text)) {
      setDraft('')
      setSince(newest) // the last answer's receipts go with it
      setStoppedAfter(null)
      field.current?.focus() // Send becomes Stop under a pointer that clicked it
    }
  }

  function stop() {
    chat.stop()
    setStoppedAfter(lastQuestion?.id ?? null)
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    if (event.dataTransfer.files.length) void attachments.add(Array.from(event.dataTransfer.files))
  }

  const composer = (
    <div className={styles.dockInner}>
      {/* With no transcript on screen yet, a failed first message is reported here. */}
      {empty && problem && (
        <Notice
          tone="critical"
          live
          title={problem.title}
          action={
            <Button size="sm" variant="ghost" onClick={chat.dismissError}>
              OK
            </Button>
          }
        >
          {problem.body}
        </Notice>
      )}
      {attachments.error && (
        <Notice
          tone="attention"
          live
          action={
            <Button size="sm" variant="ghost" onClick={attachments.dismissError}>
              OK
            </Button>
          }
        >
          {attachments.error}
        </Notice>
      )}
      <Composer
        value={draft}
        onChange={setDraft}
        onSend={send}
        onStop={stop}
        busy={busy}
        lane={chat.lane}
        attachments={attachments.items}
        uploading={attachments.uploading}
        onAttach={(files) => void attachments.add(files)}
        onRemoveAttachment={(id) => void attachments.remove(id)}
        fieldRef={field}
      />
      <p className={styles.fineprint} role="status">
        {chat.lane !== 'open' ? FINEPRINT[chat.lane] : web === null ? WEB_UNKNOWN : web.on ? WEB_ON : FINEPRINT.open}
      </p>
    </div>
  )

  return (
    <div
      className={styles.chat}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
      }}
      onDrop={onDrop}
    >
      {empty ? (
        <div className={styles.start}>
          <div className={styles.startInner}>
            <Welcome
              project={project}
              onStarter={(prompt) => {
                setDraft(prompt)
                requestAnimationFrame(() => {
                  const element = field.current
                  element?.focus()
                  element?.setSelectionRange(prompt.length, prompt.length)
                })
              }}
            />
            {composer}
          </div>
        </div>
      ) : (
        <>
          <ChatHeader title={summary ? titleOf(summary) : 'New chat'} project={project} />
          {loading ? (
            <div className={styles.loading} role="status">
              <span className="visually-hidden">Opening the conversation</span>
              <Skeleton width="55%" height={22} />
              <Skeleton width="92%" />
              <Skeleton width="86%" />
              <Skeleton width="64%" />
            </div>
          ) : (
            <Transcript
              messages={chat.messages}
              state={chat.state}
              conversationId={chat.conversationId}
              stoppedAfter={stoppedAfter}
              problem={problem}
              onRetry={() => {
                if (lastQuestion) chat.retry(lastQuestion.content)
              }}
              onDismissProblem={chat.dismissError}
              receipts={receipts}
              onUndoReceipt={(item) => {
                undo(item)
                setUndone((ids) => [...ids, item.id])
              }}
            />
          )}
          <div className={styles.dock}>{composer}</div>
        </>
      )}
      {/* Streaming text is never announced word by word; the whole answer is, once. */}
      <p className="visually-hidden" role="status">
        {chat.announcement}
      </p>
      {dragging && (
        <div className={styles.drop} aria-hidden="true">
          <Paperclip size={22} />
          Drop to attach to this conversation
        </div>
      )}
    </div>
  )
}
