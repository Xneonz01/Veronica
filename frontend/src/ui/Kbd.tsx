// A key or a shortcut, as a hint beside the thing it triggers. Hidden from screen
// readers when it only repeats what aria-keyshortcuts already says.

import { cx } from './cx'
import styles from './Kbd.module.css'

export function Kbd({ children, className }: { children: string; className?: string }) {
  return (
    <kbd className={cx(styles.kbd, className)} aria-hidden="true">
      {children}
    </kbd>
  )
}
