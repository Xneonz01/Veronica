// The files attached to this conversation, above what you are writing. An image
// says it is stored but not read: no vision model runs here, and without that
// word you could believe she had looked at it.

import { FileText, Image, X } from 'lucide-react'
import type { Attachment } from '../chat/useAttachments'
import styles from './AttachmentChips.module.css'

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function AttachmentChips({
  items,
  uploading,
  onRemove,
}: {
  items: readonly Attachment[]
  uploading: readonly string[]
  onRemove: (id: number) => void
}) {
  if (items.length === 0 && uploading.length === 0) return null
  return (
    <ul className={styles.chips} aria-label="Attached files">
      {items.map((item) => {
        const image = item.kind === 'image'
        const Icon = image ? Image : FileText
        return (
          <li key={item.id} className={styles.chip}>
            <Icon size={16} className={styles.icon} aria-hidden="true" />
            <span className={styles.text}>
              <span className={styles.name}>{item.name}</span>
              <span className={image ? styles.notRead : styles.meta}>{image ? 'Stored, not read' : size(item.size_bytes)}</span>
            </span>
            <button type="button" className={styles.remove} aria-label={`Remove ${item.name}`} onClick={() => onRemove(item.id)}>
              <X size={13} aria-hidden="true" />
            </button>
          </li>
        )
      })}
      {uploading.map((name) => (
        <li key={name} className={styles.chip} aria-busy="true">
          <span className={styles.spinner} aria-hidden="true" />
          <span className={styles.text}>
            <span className={styles.name}>{name}</span>
            <span className={styles.meta}>Reading…</span>
          </span>
        </li>
      ))}
    </ul>
  )
}
