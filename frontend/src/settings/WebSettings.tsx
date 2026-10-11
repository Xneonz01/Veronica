// Web search (ADR 0003): off until you turn it on, through a search service you choose,
// with sites she must never open. Every change applies at once, in chat and on calls.

import { useState } from 'react'
import { messageFor } from '../lib/api'
import { Button } from '../ui/Button'
import { Notice, Skeleton } from '../ui/Feedback'
import { TextArea, TextField } from '../ui/Field'
import { Segmented } from '../ui/Segmented'
import { Switch } from '../ui/Switch'
import { Group, Row } from './Group'
import { useWebSettings, type SearchService, type TestResult, type WebSettings as Settings } from './useWebSettings'
import styles from './WebSettings.module.css'

const SERVICE: Record<SearchService, string> = {
  searxng: 'A search engine you run yourself. No account and no key.',
  brave: 'Brave Search, with a key from your Brave account.',
}

// What trying the service found, by the reason the backend gives.
const TRIED: Record<string, string> = {
  no_key: 'Add a Brave Search key first.',
  unreachable: "Couldn't reach the search service. Check that it's running at that address.",
  json_off: "SearXNG is running but won't answer in JSON. Add json under search: formats: in its settings.yml.",
  bad_key: "The search service didn't accept the key.",
  rate_limited: 'The search service says too many searches. Try again in a moment.',
  offline: "Couldn't reach Veronica. Try again in a moment.",
}
const TRIED_OTHERWISE = 'The search service answered, but not with results.'
const NEEDS_HTTP = 'Start the address with http:// or https://.'

export function WebSettings() {
  const { settings, loadError, retry, save, setKey, test } = useWebSettings()
  const [failed, setFailed] = useState<string | null>(null)

  if (settings === null) {
    return (
      <Group title="Web search" bare={loadError !== null}>
        {loadError ? (
          <Notice tone="critical" live action={<Button size="sm" onClick={() => void retry()}>Try again</Button>}>
            {loadError}
          </Notice>
        ) : (
          <Skeleton height={120} />
        )}
      </Group>
    )
  }

  async function apply(task: () => Promise<unknown>) {
    setFailed(null)
    try {
      await task()
    } catch (error) {
      setFailed(messageFor(error, "Couldn't save that. Try again in a moment."))
    }
  }

  return (
    <>
      <Group
        title="Web search"
        lead="Off unless you turn it on. When it's on, only what she searches for and the pages she reads go out to the internet."
      >
        <Switch
          label="Search the web"
          description="She can look up current events and facts she doesn't know, in chat and on calls. After reading the web, she asks before she saves, writes or opens anything."
          checked={settings.on}
          onChange={(on) => void apply(() => save({ on }))}
        />
        <Row label="Search service" description={SERVICE[settings.service]}>
          <Segmented<SearchService>
            label="Search service"
            size="sm"
            value={settings.service}
            onChange={(service) => void apply(() => save({ service }))}
            options={[
              { value: 'searxng', label: 'SearXNG' },
              { value: 'brave', label: 'Brave' },
            ]}
          />
        </Row>
        {settings.service === 'searxng' ? (
          <SearxngAddress key={settings.searxngUrl} saved={settings.searxngUrl} onSave={(searxngUrl) => apply(() => save({ searxngUrl }))} />
        ) : (
          <BraveKey keySet={settings.keySet} onSave={(key) => apply(() => setKey(key))} />
        )}
        <TryService settings={settings} test={test} />
      </Group>
      <Group title="Blocked sites" lead="She never opens a page on these sites or their subdomains. One per line.">
        <BlockedSites key={settings.blockedHosts.join('\n')} saved={settings.blockedHosts} onSave={(blockedHosts) => apply(() => save({ blockedHosts }))} />
      </Group>
      {failed && (
        <Notice tone="critical" live>
          {failed}
        </Notice>
      )}
    </>
  )
}

function SearxngAddress({ saved, onSave }: { saved: string; onSave: (url: string) => Promise<void> }) {
  const [draft, setDraft] = useState(saved)
  const trimmed = draft.trim()
  const invalid = trimmed !== '' && !/^https?:\/\//i.test(trimmed)
  const changed = trimmed !== saved && trimmed !== '' && !invalid
  return (
    <Row label="SearXNG address" description="Where your SearXNG server answers, on this computer or your network.">
      <TextField
        label="SearXNG address"
        hideLabel
        value={draft}
        spellCheck={false}
        error={invalid ? NEEDS_HTTP : undefined}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => changed && void onSave(trimmed)}
        onKeyDown={(event) => event.key === 'Enter' && changed && void onSave(trimmed)}
      />
    </Row>
  )
}

function BraveKey({ keySet, onSave }: { keySet: boolean; onSave: (key: string | null) => Promise<void> }) {
  const [draft, setDraft] = useState('')
  if (keySet) {
    return (
      <Row label="Brave Search key" description="A key is saved, sealed on this computer.">
        <Button size="sm" variant="ghost" onClick={() => void onSave(null)}>
          Remove key
        </Button>
      </Row>
    )
  }
  return (
    <Row label="Brave Search key" description="Kept sealed on this computer, and never shown again.">
      <div className={styles.inline}>
        <TextField label="Brave Search key" hideLabel type="password" autoComplete="off" value={draft} onChange={(event) => setDraft(event.target.value)} />
        <Button size="sm" disabled={!draft.trim()} onClick={() => void onSave(draft.trim()).then(() => setDraft(''))}>
          Save key
        </Button>
      </div>
    </Row>
  )
}

function TryService({ settings, test }: { settings: Settings; test: () => Promise<TestResult> }) {
  const [trying, setTrying] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  return (
    <Row
      label="Try the search service"
      description="Runs one search, even while web search is off."
      below={
        result && (
          <Notice tone={result.ok ? 'positive' : 'attention'} live>
            {result.ok ? 'It answered with results.' : (TRIED[result.reason] ?? TRIED_OTHERWISE)}
          </Notice>
        )
      }
    >
      <Button
        size="sm"
        loading={trying}
        disabled={settings.service === 'brave' && !settings.keySet}
        onClick={async () => {
          setTrying(true)
          setResult(await test())
          setTrying(false)
        }}
      >
        Try it
      </Button>
    </Row>
  )
}

function BlockedSites({ saved, onSave }: { saved: string[]; onSave: (hosts: string[]) => Promise<void> }) {
  const [draft, setDraft] = useState(saved.join('\n'))
  const hosts = draft.split('\n').map((line) => line.trim()).filter(Boolean)
  const changed = hosts.join('\n') !== saved.join('\n')
  return (
    <div className={styles.sites}>
      <TextArea label="Blocked sites" hideLabel rows={4} spellCheck={false} placeholder="ads.example.com" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <div>
        <Button size="sm" disabled={!changed} onClick={() => void onSave(hosts)}>
          Save
        </Button>
      </div>
    </div>
  )
}
