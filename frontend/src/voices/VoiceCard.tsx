// One voice she can speak in: its name, where it came from, a sample to hear,
// and switching to it. Each voice draws its own small waveform, made from its id,
// so voices are told apart at a glance the way faces are. The built-in voice
// can't be deleted; deleting the one in use falls back to it.

import { Check, Play, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { VoiceInfo } from '../audio/useVoices'
import { relativeTime } from '../memory/format'
import { Badge } from '../ui/Badge'
import { Button, IconButton } from '../ui/Button'
import { cx } from '../ui/cx'
import styles from './VoiceCard.module.css'
import { waveOf } from './wave'

const SOURCE: Record<string, string> = {
  seed: 'Built in',
  upload: 'From your clips',
  record: 'Recorded here',
}

export function VoiceCard({
  voice,
  onUse,
  onHear,
  onDelete,
}: {
  voice: VoiceInfo
  onUse: (id: string) => void
  onHear: (id: string) => Promise<void>
  onDelete: (voice: VoiceInfo) => void
}) {
  const [hearing, setHearing] = useState(false)
  const builtIn = voice.source === 'seed'

  async function hear() {
    setHearing(true)
    try {
      await onHear(voice.voice_id)
    } finally {
      setHearing(false)
    }
  }

  return (
    <article className={cx(styles.card, voice.active && styles.active)} aria-label={voice.name}>
      <span className={styles.wave} aria-hidden="true">
        {waveOf(voice.voice_id).map((height, index) => (
          <span key={index} style={{ height: `${Math.round(height * 100)}%` }} />
        ))}
      </span>
      <div className={styles.head}>
        <h3 className={styles.name}>{voice.name}</h3>
        {voice.active && (
          <Badge tone="assistant" size="sm" icon={<Check size={12} aria-hidden="true" />}>
            In use
          </Badge>
        )}
      </div>
      <p className={styles.meta}>
        {SOURCE[voice.source] ?? 'Added'} · {relativeTime(voice.created_at) ?? 'earlier'}
      </p>
      <div className={styles.actions}>
        <Button size="sm" variant="secondary" icon={<Play size={14} aria-hidden="true" />} loading={hearing} onClick={() => void hear()}>
          Hear a sample
        </Button>
        {!voice.active && (
          <Button size="sm" variant="ghost" onClick={() => onUse(voice.voice_id)}>
            Use this voice
          </Button>
        )}
        {!builtIn && (
          <IconButton
            size="sm"
            className={styles.delete}
            label={`Delete ${voice.name}`}
            icon={<Trash2 size={15} aria-hidden="true" />}
            onClick={() => onDelete(voice)}
          />
        )}
      </div>
    </article>
  )
}
