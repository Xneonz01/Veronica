// The design page (development builds only, at #/design): every token, material
// and component, working, in one theme at a time. The colour panels always show
// both. Anything a screen uses is judged here first.
//
// The theme switch here is the app's own (the same one as in Settings), so a
// specimen is always judged in the theme the screens are using.

import { Monitor, Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { setTheme, useTheme } from '../lib/theme'
import { Mark } from '../ui/Mark'
import { Segmented } from '../ui/Segmented'
import { CallSpecimens } from './CallSpecimens'
import { Controls } from './Controls'
import { ConversationStates } from './ConversationStates'
import styles from './DesignSystem.module.css'
import { FeedbackSpecimens } from './FeedbackSpecimens'
import { Foundations } from './Foundations'
import { LightStudy } from './LightStudy'
import { Motion } from './Motion'
import { Overlays } from './Overlays'

const SECTIONS = [
  { id: 'compositions', label: 'Conversation' },
  { id: 'call', label: 'Call' },
  { id: 'colour', label: 'Colour' },
  { id: 'type', label: 'Type' },
  { id: 'material', label: 'Material' },
  { id: 'controls', label: 'Controls' },
  { id: 'overlays', label: 'Overlays' },
  { id: 'feedback', label: 'Feedback' },
  { id: 'motion', label: 'Motion' },
  { id: 'light-study', label: 'Light study' },
]
const SECTION_IDS = SECTIONS.map((section) => section.id)

export default function DesignSystem() {
  const theme = useTheme()
  const current = useCurrentSection(SECTION_IDS)

  return (
    <div className={styles.page}>
      <header className={styles.bar}>
        <div className={styles.brand}>
          <Mark size={22} />
          <span className={styles.brandName}>Design system</span>
        </div>
        <nav aria-label="Sections" className={styles.nav}>
          {SECTIONS.map((section) => (
            <button
              key={section.id}
              type="button"
              className={styles.navLink}
              aria-current={current === section.id ? 'location' : undefined}
              onClick={() => jumpTo(section.id)}
            >
              {section.label}
            </button>
          ))}
        </nav>
        <Segmented
          label="Theme"
          size="sm"
          value={theme}
          onChange={setTheme}
          options={[
            { value: 'system', label: <Monitor size={15} aria-hidden="true" />, title: 'System' },
            { value: 'light', label: <Sun size={15} aria-hidden="true" />, title: 'Light' },
            { value: 'dark', label: <Moon size={15} aria-hidden="true" />, title: 'Dark' },
          ]}
        />
      </header>
      <main className={styles.content}>
        <ConversationStates />
        <CallSpecimens />
        <Foundations />
        <Controls />
        <Overlays />
        <FeedbackSpecimens />
        <Motion />
        <LightStudy />
      </main>
    </div>
  )
}

function jumpTo(id: string) {
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  document.getElementById(id)?.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' })
}

/** The section crossing a band a third of the way down the window. */
function useCurrentSection(ids: string[]): string {
  const [current, setCurrent] = useState(ids[0])
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const crossing = entries.find((entry) => entry.isIntersecting)
        if (crossing) setCurrent(crossing.target.id)
      },
      { rootMargin: '-30% 0px -60% 0px' },
    )
    for (const id of ids) {
      const element = document.getElementById(id)
      if (element) observer.observe(element)
    }
    return () => observer.disconnect()
  }, [ids])
  return current
}
