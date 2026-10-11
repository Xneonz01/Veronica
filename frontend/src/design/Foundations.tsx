// Tokens as specimens: colour (both themes side by side, contrast measured
// live), the type scale, the materials over a busy ground, and the mark.

import { Mark } from '../ui/Mark'
import { Surface, type Material } from '../ui/Surface'
import { ContrastTable } from './ContrastTable'
import styles from './Foundations.module.css'
import { Section, Tile, Tiles } from './Specimen'

const SURFACES = ['background', 'surface', 'surface-muted', 'surface-elevated', 'muted', 'muted-strong']
const INKS = ['foreground', 'muted-foreground', 'subtle-foreground', 'faint-foreground']
const ACCENTS = ['assistant', 'primary', 'info', 'warning', 'destructive', 'success']
const TINTS = [
  { tone: 'assistant', label: 'Veronica' },
  { tone: 'success', label: 'Done' },
  { tone: 'info', label: 'From your files' },
  { tone: 'warning', label: 'Needs you' },
  { tone: 'destructive', label: 'Refused' },
]
const TYPE = [
  { token: '4xl', role: 'Figure', weight: 'semibold', sample: '412 ms' },
  { token: '3xl', role: 'Greeting', weight: 'semibold', sample: 'Good evening, Rowan.' },
  { token: 'xl', role: 'Your question', weight: 'semibold', sample: 'What did I note about the roof?' },
  { token: 'lg', role: 'Section', weight: 'semibold', sample: 'What she remembers' },
  { token: 'base', role: 'Answer', weight: 'regular', sample: 'You noted a leak above the back bedroom.' },
  { token: 'md', role: 'Interface', weight: 'medium', sample: 'Searched your files' },
  { token: 'sm', role: 'Meta', weight: 'regular', sample: 'Updated 2 minutes ago' },
  { token: 'xs', role: 'Caption', weight: 'medium', sample: 'Sources' },
]
const MATERIALS: { material: Material; note: string }[] = [
  { material: 'flat', note: 'Content' },
  { material: 'subtle', note: 'Quiet chrome' },
  { material: 'glass', note: 'Bars, cards' },
  { material: 'elevated', note: 'Sheets, menus' },
  { material: 'interactive', note: 'Point at it' },
  { material: 'emissive', note: 'Her, once' },
]
const MARK_SIZES = [17, 22, 26, 34, 40]

export function Foundations() {
  return (
    <>
      <Section
        id="colour"
        title="Colour"
        lead="Both themes side by side, whatever the switch above says. Every pair is measured from the live tokens."
      >
        <div className={styles.panels}>
          <ColourPanel theme="light" />
          <ColourPanel theme="dark" />
        </div>
      </Section>
      <Section id="type" title="Type" lead="Inter with its optical sizes. Hierarchy from size, weight and colour; weights stop at 600.">
        <TypeScale />
      </Section>
      <Section
        id="material"
        title="Material"
        lead="Content is flat, chrome is glass, a result may be glass, and her own presence may glow, once per screen."
      >
        <Materials />
        <Tiles>
          <Tile label="Mark" note="The speaking orb, at the sizes it ships">
            <div className={styles.marks}>
              {MARK_SIZES.map((size) => (
                <span key={size} className={styles.markSize}>
                  <Mark size={size} />
                  <span className={`${styles.token} num`}>{size}</span>
                </span>
              ))}
            </div>
          </Tile>
        </Tiles>
      </Section>
    </>
  )
}

function ColourPanel({ theme }: { theme: 'light' | 'dark' }) {
  return (
    <div data-theme={theme} className={styles.panel}>
      <h3 className={styles.panelTitle}>{theme === 'light' ? 'Light' : 'Dark'}</h3>
      <SwatchRow label="Surfaces" tokens={SURFACES} />
      <SwatchRow label="Ink" tokens={INKS} />
      <SwatchRow label="Accents" tokens={ACCENTS} />
      <div className={styles.tints}>
        {TINTS.map(({ tone, label }) => (
          <span
            key={tone}
            className={styles.tint}
            style={{ background: `var(--${tone}-soft)`, color: `var(--${tone}-on-soft)` }}
          >
            {label}
          </span>
        ))}
      </div>
      <ContrastTable />
    </div>
  )
}

function SwatchRow({ label, tokens }: { label: string; tokens: string[] }) {
  return (
    <div className={styles.swatchRow}>
      <span className={styles.rowLabel}>{label}</span>
      <div className={styles.swatches}>
        {tokens.map((token) => (
          <span key={token} className={styles.swatch}>
            <span className={styles.chip} style={{ background: `var(--${token})` }} />
            <code className={styles.token}>{token}</code>
          </span>
        ))}
      </div>
    </div>
  )
}

function TypeScale() {
  return (
    <div className={styles.typeScale}>
      {TYPE.map((step) => (
        <div key={step.token} className={styles.typeRow}>
          <span className={styles.typeMeta}>
            {step.role} · {step.token}
          </span>
          <span
            className={styles.typeSample}
            style={{
              fontSize: `var(--text-${step.token})`,
              fontWeight: `var(--weight-${step.weight})`,
            }}
          >
            {step.sample}
          </span>
        </div>
      ))}
    </div>
  )
}

function Materials() {
  return (
    <div className={styles.ground}>
      <p className={styles.groundText} aria-hidden="true">
        Glass reads as glass only over something. These lines and colours sit behind every tile.
      </p>
      <div className={styles.materials}>
        {MATERIALS.map(({ material, note }) => (
          <Surface key={material} material={material} radius="xl" className={styles.material}>
            <span className={styles.materialName}>{material}</span>
            <span className={styles.materialNote}>{note}</span>
          </Surface>
        ))}
      </div>
    </div>
  )
}
