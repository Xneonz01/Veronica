// An answer's text as React elements, from the tree blocks.ts and inline.ts
// build. While it streams, a caret sits exactly where the next words will land.

import { Check, Copy } from 'lucide-react'
import { Fragment, useMemo, useState, type ReactNode } from 'react'
import type { Citation } from '../chat/types'
import { cx } from '../ui/cx'
import { parseBlocks, type Block, type ListBlock } from './blocks'
import { parseInline, type Inline } from './inline'
import styles from './RichText.module.css'

export type RichTextProps = {
  source: string
  citations?: readonly Citation[]
  onCite?: (n: number) => void
  streaming?: boolean
}

export function RichText({ source, citations = [], onCite, streaming = false }: RichTextProps) {
  const blocks = useMemo(() => parseBlocks(source), [source])
  const sources = useMemo(() => new Set(citations.map((citation) => citation.n)), [citations])
  const context: Context = { sources, onCite }
  const caret = streaming ? <span className={styles.caret} aria-hidden="true" /> : null
  return (
    <div className={styles.text}>
      {blocks.map((block, index) => (
        <BlockView key={index} block={block} context={context} tail={index === blocks.length - 1 ? caret : null} />
      ))}
      {blocks.length === 0 && caret}
    </div>
  )
}

type Context = { sources: ReadonlySet<number>; onCite?: (n: number) => void }

function BlockView({ block, context, tail }: { block: Block; context: Context; tail: ReactNode }) {
  switch (block.type) {
    case 'paragraph':
      return (
        <p>
          <Inlines text={block.text} context={context} />
          {tail}
        </p>
      )
    case 'heading': {
      const Heading = block.level <= 2 ? 'h3' : 'h4'
      return (
        <Heading className={block.level <= 2 ? styles.h3 : styles.h4}>
          <Inlines text={block.text} context={context} />
          {tail}
        </Heading>
      )
    }
    case 'list':
      return <ListView list={block} context={context} tail={tail} />
    case 'code':
      return <CodeBlock lang={block.lang} code={block.text} tail={tail} />
    case 'quote':
      return (
        <blockquote className={styles.quote}>
          <Inlines text={block.text} context={context} />
          {tail}
        </blockquote>
      )
    case 'table':
      return (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                {block.header.map((cell, index) => (
                  <th key={index} style={{ textAlign: block.align[index] ?? undefined }}>
                    <Inlines text={cell} context={context} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, index) => (
                    <td key={index} style={{ textAlign: block.align[index] ?? undefined }}>
                      <Inlines text={cell} context={context} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {tail}
        </div>
      )
    case 'rule':
      return <hr className={styles.rule} />
  }
}

function ListView({ list, context, tail }: { list: ListBlock; context: Context; tail: ReactNode }) {
  const Tag = list.ordered ? 'ol' : 'ul'
  const last = list.items.length - 1
  return (
    <Tag className={cx(styles.list, list.ordered && styles.ordered)} start={list.ordered ? list.start : undefined}>
      {list.items.map((item, index) => (
        <li key={index}>
          <Inlines text={item.text} context={context} />
          {item.children && <ListView list={item.children} context={context} tail={index === last ? tail : null} />}
          {index === last && !item.children && tail}
        </li>
      ))}
    </Tag>
  )
}

function Inlines({ text, context }: { text: string; context: Context }) {
  const pieces = useMemo(() => parseInline(text, context.sources), [text, context.sources])
  return <>{pieces.map((piece, index) => <InlineView key={index} piece={piece} context={context} />)}</>
}

function InlineView({ piece, context }: { piece: Inline; context: Context }): ReactNode {
  switch (piece.type) {
    case 'text':
      return piece.text
    case 'strong':
      return <strong>{piece.children.map((child, index) => <InlineView key={index} piece={child} context={context} />)}</strong>
    case 'em':
      return <em>{piece.children.map((child, index) => <InlineView key={index} piece={child} context={context} />)}</em>
    case 'code':
      return <code className={styles.code}>{piece.text}</code>
    case 'break':
      return <br />
    case 'keep':
      return (
        <span className={styles.keep}>
          {piece.children.map((child, index) => <InlineView key={index} piece={child} context={context} />)}
        </span>
      )
    case 'cite':
      return (
        <button type="button" className={styles.cite} onClick={() => context.onCite?.(piece.n)} aria-label={`Source ${piece.n}`}>
          {piece.n}
        </button>
      )
    case 'link':
      // The desktop app can't open a browser from here yet, so the address is
      // shown rather than hidden behind a link that would do nothing.
      return (
        <Fragment>
          {piece.text} <span className={styles.url}>({displayUrl(piece.url)})</span>
        </Fragment>
      )
  }
}

function displayUrl(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')
}

function CodeBlock({ lang, code, tail }: { lang: string; code: string; tail: ReactNode }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard?.writeText(code).then(
      () => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1600)
      },
      () => setCopied(false), // a refused clipboard isn't worth an error
    )
  }
  return (
    <figure className={styles.codeBlock}>
      <figcaption className={styles.codeBar}>
        <span>{lang || 'Code'}</span>
        <button type="button" className={styles.copy} onClick={copy} aria-live="polite">
          {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </figcaption>
      <pre className={styles.pre}>
        <code>{code}</code>
        {tail}
      </pre>
    </figure>
  )
}
