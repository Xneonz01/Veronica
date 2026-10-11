// Which Veronica this is, whether the last run ended cleanly, whether this computer has
// what she needs, and the patch notes for every release, each one a disclosure. The
// newest starts open.

import { Button } from '../ui/Button'
import { Badge } from '../ui/Badge'
import { Disclosure } from '../ui/Disclosure'
import { EmptyState, Notice, Skeleton } from '../ui/Feedback'
import { Mark } from '../ui/Mark'
import { useAbout, type About, type NoteSection, type ReleaseNotes } from '../system/useAbout'
import styles from './AboutSettings.module.css'
import { ComputerChecks } from './ComputerChecks'
import { Group } from './Group'
import { releaseDate } from './releaseDate'

const SECTIONS: { name: NoteSection; title: string }[] = [
  { name: 'new', title: 'New' },
  { name: 'improved', title: 'Improved' },
  { name: 'fixed', title: 'Fixed' },
  { name: 'known', title: 'Known issues' },
]

export function AboutSettings() {
  const { about, error, retry } = useAbout()
  return (
    <>
      <Group title="Version" bare={!about && error !== null}>
        {about ? (
          <Version about={about} />
        ) : error ? (
          <Notice tone="critical" live action={<Button size="sm" onClick={() => void retry()}>Try again</Button>}>
            {error}
          </Notice>
        ) : (
          <div className={styles.loading} role="status">
            <span className="visually-hidden">Loading the version</span>
            <Skeleton width={36} height={36} radius={18} />
            <Skeleton width={200} />
          </div>
        )}
      </Group>

      <ComputerChecks />

      {about && (
        <Group title="Patch notes" lead="What changed in every release, newest first.">
          {about.releases.length > 0 ? (
            about.releases.map((release, index) => <Release key={release.version} release={release} open={index === 0} />)
          ) : (
            <EmptyState title="No patch notes yet">They appear here with the first release.</EmptyState>
          )}
        </Group>
      )}
    </>
  )
}

function Version({ about }: { about: About }) {
  return (
    <>
      <div className={styles.version}>
        <Mark size={36} />
        <div className={styles.name}>
          <p className={styles.product}>
            Veronica <span className="num">{about.version}</span>
          </p>
          {about.label && <p className={styles.label}>{about.label}</p>}
        </div>
        <Badge size="sm">{about.channel}</Badge>
      </div>
      {about.previousRunClean === false && (
        <Notice tone="attention" title="The last session didn't close cleanly">
          If something went wrong, the diagnostics script in the backend folder shows what was recorded.
        </Notice>
      )}
    </>
  )
}

function Release({ release, open }: { release: ReleaseNotes; open: boolean }) {
  return (
    <Disclosure
      open={open}
      summary={
        <>
          <span className={`${styles.number} num`}>{release.version}</span>
          <span className={styles.title}>{release.label}</span>
          <span className={styles.date}>{releaseDate(release.date)}</span>
        </>
      }
    >
      <div className={styles.notes}>
        {SECTIONS.map(({ name, title }) => {
          const notes = release.sections[name]
          if (!notes || notes.length === 0) return null
          return (
            <div key={name} className={styles.section}>
              <h3 className={styles.sectionTitle}>{title}</h3>
              <ul className={styles.list}>
                {notes.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            </div>
          )
        })}
      </div>
    </Disclosure>
  )
}
