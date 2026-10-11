// The theme, chosen from three small pictures of the app. Each picture is drawn in
// its own theme (a data-theme island), so you see what you are choosing before you
// choose it. Native radios: the arrow keys and a screen reader work without help.

import { Check } from 'lucide-react'
import { setTheme, THEMES, useTheme, type Theme } from '../lib/theme'
import styles from './ThemeChoice.module.css'

const NAME: Record<Theme, string> = { system: 'System', light: 'Light', dark: 'Dark' }

export function ThemeChoice() {
  const theme = useTheme()
  return (
    <fieldset className={styles.choices}>
      <legend className="visually-hidden">Theme</legend>
      {THEMES.map((option) => (
        <label key={option} className={styles.choice}>
          <input
            type="radio"
            name="theme"
            value={option}
            checked={theme === option}
            onChange={() => setTheme(option)}
            className={`visually-hidden ${styles.input}`}
          />
          <span className={styles.preview} aria-hidden="true">
            {option === 'system' ? (
              <>
                <Window theme="light" />
                <Window theme="dark" split />
              </>
            ) : (
              <Window theme={option} />
            )}
          </span>
          <span className={styles.name}>
            {NAME[option]}
            <Check size={14} className={styles.check} aria-hidden="true" />
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/** A few strokes of the app: the sidebar, the conversation sheet, a question and
    her answer, and the composer. */
function Window({ theme, split = false }: { theme: 'light' | 'dark'; split?: boolean }) {
  return (
    <span data-theme={theme} className={split ? `${styles.window} ${styles.split}` : styles.window}>
      <span className={styles.rail} />
      <span className={styles.sheet}>
        <span className={styles.question} />
        <span className={styles.answer}>
          <span className={styles.mark} />
          <span className={styles.lines} />
        </span>
        <span className={styles.composer} />
      </span>
    </span>
  )
}
