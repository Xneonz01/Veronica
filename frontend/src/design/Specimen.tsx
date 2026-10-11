// The design page's layout pieces: a section with its heading, and a tile that
// frames one specimen with a caption. Nothing here is used by the product.

import type { ReactNode } from 'react'
import { cx } from '../ui/cx'
import styles from './Specimen.module.css'

export function Section({
  id,
  title,
  lead,
  children,
}: {
  id: string
  title: string
  lead?: ReactNode
  children: ReactNode
}) {
  return (
    <section id={id} className={styles.section} aria-labelledby={`${id}-title`}>
      <header className={styles.head}>
        <h2 id={`${id}-title`} className={styles.title}>
          {title}
        </h2>
        {lead != null && <p className={styles.lead}>{lead}</p>}
      </header>
      {children}
    </section>
  )
}

export function Tiles({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <div className={cx(styles.tiles, wide && styles.wideTiles)}>{children}</div>
}

export function Tile({
  label,
  note,
  children,
  span = false,
}: {
  label: string
  note?: ReactNode
  children: ReactNode
  /** Take the whole row. */
  span?: boolean
}) {
  return (
    <figure className={cx(styles.tile, span && styles.span)}>
      <div className={styles.stage}>{children}</div>
      <figcaption className={styles.caption}>
        <span className={styles.captionLabel}>{label}</span>
        {note != null && <span className={styles.captionNote}>{note}</span>}
      </figcaption>
    </figure>
  )
}

/** A wrapping row of specimens with even gaps. */
export function Row({ children }: { children: ReactNode }) {
  return <div className={styles.row}>{children}</div>
}

/** Specimens stacked, full width. */
export function Stack({ children }: { children: ReactNode }) {
  return <div className={styles.stack}>{children}</div>
}
