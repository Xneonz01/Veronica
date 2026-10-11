// The states every surface owes the person using it — loading, working, empty,
// a notice for each tone — and the small carriers of status: badges and figures.

import { AudioLines, Check, FileText, ShieldCheck, Sparkles, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Badge, type BadgeTone } from '../ui/Badge'
import { Button } from '../ui/Button'
import { EmptyState, Notice, Skeleton, Working } from '../ui/Feedback'
import { Metric } from '../ui/Metric'
import styles from './FeedbackSpecimens.module.css'
import { Row, Section, Stack, Tile, Tiles } from './Specimen'

const BADGES: { tone: BadgeTone; label: string; icon?: typeof Check }[] = [
  { tone: 'neutral', label: 'Draft' },
  { tone: 'assistant', label: 'Veronica', icon: Sparkles },
  { tone: 'success', label: 'Saved', icon: Check },
  { tone: 'info', label: 'From your files', icon: FileText },
  { tone: 'warning', label: 'Needs you', icon: TriangleAlert },
  { tone: 'danger', label: 'Refused' },
]

const LATENCIES = ['412', '386', '455', '371']

export function FeedbackSpecimens() {
  return (
    <Section
      id="feedback"
      title="Feedback and status"
      lead="No dead ends and no raw errors: every surface says what's happening, what's empty and what to do next."
    >
      <Tiles wide>
        <Tile label="Notices" note="An icon per tone and a sentence; colour never alone">
          <Stack>
            <Notice title="Connected to the model">Answers come from Qwen on this computer.</Notice>
            <Notice tone="attention" title="The speech model is still loading" action={<Button size="sm">Wait with me</Button>}>
              Calls start in about a minute.
            </Notice>
            <Notice tone="critical" title="I can't reach the backend" action={<Button size="sm">Retry</Button>}>
              I'll keep trying on my own every few seconds.
            </Notice>
            <Notice tone="positive" title="Remembered">You prefer short answers in the morning.</Notice>
          </Stack>
        </Tile>
        <Tile label="Empty" note="Start-aligned, calm, with the next step">
          <EmptyState
            icon={<AudioLines size={22} aria-hidden="true" />}
            title="No voices yet"
            action={<Button variant="primary">Add a voice</Button>}
          >
            Record a minute of anyone who agreed to it, and she can speak in that voice.
          </EmptyState>
        </Tile>
        <LoadingTile />
        <Tile label="Badges" note="A word and a shape, never colour alone">
          <Stack>
            <Row>
              {BADGES.map(({ tone, label, icon: Icon }) => (
                <Badge key={tone} tone={tone} icon={Icon ? <Icon size={13} aria-hidden="true" /> : undefined}>
                  {label}
                </Badge>
              ))}
            </Row>
            <Row>
              <Badge tone="assistant" solid icon={<ShieldCheck size={13} aria-hidden="true" />}>
                Runs on this computer
              </Badge>
              <Badge size="sm">sm</Badge>
              <Badge size="sm" tone="info">
                3 sources
              </Badge>
            </Row>
          </Stack>
        </Tile>
      </Tiles>
      <Figures />
    </Section>
  )
}

function LoadingTile() {
  return (
    <Tile label="Loading and working" note="A skeleton shaped like the rows it becomes">
      <Stack>
        <Working>Reading your documents</Working>
        <div className={styles.rows} aria-hidden="true">
          {[72, 54, 64].map((width) => (
            <div key={width} className={styles.skeletonRow}>
              <Skeleton width={32} height={32} radius={8} />
              <div className={styles.skeletonText}>
                <Skeleton width={`${width}%`} height={12} />
                <Skeleton width="36%" height={10} />
              </div>
            </div>
          ))}
        </div>
      </Stack>
    </Tile>
  )
}

function Figures() {
  const [index, setIndex] = useState(0)
  return (
    <Tiles>
      <Tile label="Metric" note="Shown exactly as measured; changes slide, never count">
        <Metric
          label="First audio after you stop"
          value={LATENCIES[index]}
          unit="ms"
          delta={index === 0 ? undefined : deltaFrom(LATENCIES[index - 1], LATENCIES[index])}
          caption="Median of the last 20 turns"
          footer={
            <Button size="sm" variant="ghost" onClick={() => setIndex((was) => (was + 1) % LATENCIES.length)}>
              Next value
            </Button>
          }
        />
      </Tile>
      <Tile label="Metric, large, tinted" note="One headline figure per view">
        <Metric size="lg" tone="assistant" material="glass" label="Remembered" value="128" unit="facts" caption="12 waiting for your review" />
      </Tile>
    </Tiles>
  )
}

/** The change between two printed figures, with a real minus sign. */
function deltaFrom(before: string, after: string): string {
  const change = Number(after) - Number(before)
  if (change === 0) return '0 ms'
  return `${change > 0 ? '+' : '−'}${Math.abs(change)} ms`
}
