# Veronica design system

The tokens and rules the interface is built from. If a value isn't here, add it here
first, then use it. The implementation is `frontend/src/styles/`: five token files and
a base layer. Components are CSS modules that read tokens and never a literal. In
development builds, `#/design` renders every token, the materials and two reference
compositions in both themes side by side; judge a change there before it reaches a
screen.

**Status (2026-09-24).** The foundations below are in place. The screens are being
rebuilt on them one by one; until then the older screens still read the previous
tokens (kept at the bottom of `frontend/src/index.css`) and the app stays pinned to
the dark theme. Component rules are added to §8 as each component lands.

## 0. Direction

A calm, modern desktop assistant: as easy to live with as the best chat apps, and
unmistakably Veronica. The rules that produce the feel:

1. **Content is flat, chrome is glass.** Reading surfaces (answers, lists, long text)
   are flat and opaque. Glass — a translucent fill, a blur, one hairline edge, one top
   highlight, one shadow — is for chrome (the composer, toolbars), overlays (sheets,
   popovers, the palette) and results she produced. No page-sized blur, anywhere.
2. **The accent means Veronica.** Emerald is her presence: her mark, the composer's
   focus halo, thinking, voice, focus rings, the primary action, and a finished action.
   Blue is information you explore (links, citations, data). Amber is attention. Red is
   only refusal or failure. One dominant accent per context.
3. **One emissive thing per screen.** Only her presence may glow, and only once.
4. **The conversation is a document, not a chat log.** No bubbles. Your question is a
   heading; her answer is typeset prose with her mark in a narrow gutter, her results as
   cards above the prose, and where it came from below it.
5. **Motion carries information or it does not ship.** Nothing loops except to say
   "working" or "listening"; nothing floats; nothing moves while you read.
6. **Honesty is a visual feature.** Every answer can say what kind of statement it is;
   caveats are shown, quiet and never collapsed; nothing says "saved" until the backend
   said so; an unreachable backend is said plainly.
7. **Figures are tabular** (`.num`), shown exactly as produced, changed by replacement.
8. **Input-aware sizing.** Quiet 30–38 px controls for a mouse; 44 px targets under
   `(pointer: coarse)`; hover styles only under `(hover: hover)`.
9. **Selected is a pane, not a block.** A selected row is a translucent pane with a
   hairline, an emerald icon and one small indicator — never a filled emerald block.
10. **Nothing decorative without a job.** No illustrations in empty states, no gradients
    but her glow, no weights above 600.

## 1. Colour

Every colour is written once as `light-dark(light, dark)` in
`styles/tokens/semantic.css`; components read only semantic tokens. Ramps
(`styles/tokens/primitives.css`, read only by the semantic layer):

| Ramp | Use | Notes |
|---|---|---|
| slate `--neutral-*` (hue 240) | dark theme | the blue-black Veronica has always lived on |
| porcelain `--porcelain-*` (hue 172 → 220) | light theme | a trace of celadon at the light end, shed toward a cool near-black ink |
| emerald `--emerald-300…800` | Veronica, primary, success | |
| blue `--blue-400…700` | information | |
| amber `--amber-400…900` | attention | |
| red `--red-400…700` | refusal, failure | |

Roles used every day:

| Role | Token | Light | Dark |
|---|---|---|---|
| Page ground | `--background` | porcelain-50 | neutral-950 |
| Content sheet | `--surface` | porcelain-25 | neutral-925 |
| Raised (cards, popovers) | `--surface-elevated` | porcelain-0 | `oklch(0.216 0.005 240)` |
| Quiet fill / hover | `--muted`, `--muted-strong`, `--muted-stronger` | porcelain-100/200/300 | neutral-875/850/800 |
| Ink | `--foreground` | porcelain-900 | `oklch(0.95 0.004 240)` |
| Secondary text | `--muted-foreground` | porcelain-700 | `oklch(0.79 0.006 240)` |
| Tertiary text | `--subtle-foreground` | porcelain-600 | `oklch(0.66 0.008 240)` |
| Decoration only, never text | `--faint-foreground` | porcelain-400 | `oklch(0.44 0.008 240)` |
| Hairline | `--border`, `--border-strong` | porcelain hue at 10% / 18% | white at 7.5% / 13% |
| Veronica | `--assistant` | emerald-700 | emerald-400 |
| Primary fill / its ink | `--primary` / `--primary-foreground` | emerald-700 / white | emerald-500 / `oklch(0.2 0.04 165)` |
| Focus ring | `--ring` | emerald-700 at 86% | emerald-400 at 70% |
| Done | `--success` | emerald-700 | emerald-400 |
| Information | `--info` | blue-600 | blue-400 |
| Attention | `--warning` | amber-800 | amber-400 |
| Refusal, failure | `--destructive` | red-700 | red-400 |

Soft fills (`--assistant-soft`, `--success-soft`, `--info-soft`, `--warning-soft`,
`--destructive-soft`) always pair with their `*-on-soft` ink.

**Measured contrast** (WCAG 2 relative luminance; OKLCH converted to sRGB; translucent
colours composited in gamma sRGB, as browsers do; the calculator reproduces known pairs
to within 0.02). Light: on the page / on the sheet. Dark: on the sheet.

| Pair | Light | Dark |
|---|---|---|
| Ink | 16.9 / 18.0 | 16.2 |
| Secondary text | 7.7 / 8.2 | 9.7 |
| Tertiary text | 5.0 / 5.4 (4.7 on a muted fill) | 6.0 (5.5 on a muted fill) |
| Decoration (`--faint-foreground`) | 2.2 | 2.4 |
| Veronica's emerald as text | 5.0 / 5.3 | 10.6 |
| Primary label on primary fill | 5.3 | 7.7 (white would be 2.3, a fail) |
| Focus ring (needs 3.0) | 3.9 | 5.8 |
| Soft-fill pairs | 5.6–6.2 | 6.6–9.0 |
| Information / attention / refusal as text | 4.9 / 6.1 / 6.5 | 9.8 / 11.7 / 8.2 |

Re-measure any pair whose tokens change, and update this table.

## 2. Material

`styles/tokens/material.css`. The glass recipe:

```css
background: var(--glass-bg);            /* or --glass-subtle-bg / --glass-elevated-bg */
backdrop-filter: blur(var(--blur-md)) saturate(var(--glass-saturate));
box-shadow:
  inset 0 0 0 1px var(--glass-edge),    /* a hairline in the page's own hue */
  inset 0 1px 0 0 var(--glass-top),     /* a faintly cool top highlight: the refraction */
  var(--shadow-glass);                  /* or --shadow-subtle / --shadow-elevated */
```

- Blur: `--blur-sm` 8 px (chips, cards), `--blur-md` 18 px (bars, the composer),
  `--blur-lg` 30 px (sheets, the palette, popovers).
- Tints (`--tint-subtle`, `--tint`) are for controls resting on glass or on a flat
  plane: the same fill and edge, no second blur.
- Shadows are cool in light (a shadow cast on porcelain) and black in dark.
- `--glow-assistant` is the only glow.
- Without `backdrop-filter`, the glass fills become opaque; tints stay translucent.
- Five surfaces: flat, subtle, glass, elevated, emissive (elevated + an emerald edge +
  her glow).

## 3. Type

`styles/tokens/type.css`. **Inter Variable**, self-hosted
(`@fontsource-variable/inter`, weight and optical-size axes; no font CDN), with
`font-feature-settings: 'cv11' 1, 'ss01' 1` (single-storey a, open digits) and
`font-optical-sizing: auto`. A system monospace stack for code and identifiers.

| Token | Size | Use |
|---|---|---|
| `--text-xs` | 12 px | captions, labels |
| `--text-sm` | 13 px | meta, hints |
| `--text-md` | 14 px | interface |
| `--text-base` | 16 px | answers, body |
| `--text-lg` | 18 px | section titles |
| `--text-xl` | 22 px | your question, as a heading |
| `--text-2xl` | 28 px | page titles |
| `--text-3xl` | 36 px | the greeting |
| `--text-4xl` | 48 px | a headline figure |

Leading: tight 1.12, snug 1.3, normal 1.55; long answers 1.62. Tracking: display
−0.03em, title −0.018em, interface −0.006em. Weights 400 / 500 / 600 only. Headings
`text-wrap: balance`, paragraphs `text-wrap: pretty`. Prose measure 68ch.

## 4. Space, radius, stacking, layout

- Space (4 px base): 2, 4, 8, 12, 16, 24, 32, 48, 64, 96 (`--space-3xs` … `--space-4xl`).
- Radius: 6 / 8 / 12 / 16 / 20 / 24 / pill (`--radius-xs` … `--radius-2xl`,
  `--radius-pill`).
- Stacking: sticky 10, nav 20, dropdown 30, toast 60, tooltip 70. Native `<dialog>` uses
  the top layer.
- Layout: sidebar 240 px, rail 72 px, reading page 760 px, dense page 1040 px,
  conversation column 760 px, measure 68ch, tap target 44 px.

## 5. Motion

`styles/tokens/motion.css`.

- Durations: `--motion-fast` 150 ms (hover, press, colour), `--motion-default` 220 ms
  (most transitions), `--motion-slow` 340 ms (things that travel).
- Curves: `--ease-standard` (decelerating, no overshoot; the default), `--ease-spring`
  (one soft overshoot, only where something arrives), `--ease-in-out` (the few loops),
  `--ease-sheet` (sheets and the palette).
- **Animations are used through tokens** (`animation: var(--anim-rise-in)`), never by
  keyframe name inside a CSS module: CSS Modules would rename the name and the
  animation would silently never run. The keyframes live in `styles/base.css`.
  Available: `--anim-fade-in`, `--anim-rise-in`, `--anim-rise-in-slow`, `--anim-pop-in`,
  `--anim-pop-in-spring`, `--anim-unfold`, `--anim-pulse`, `--anim-breathe`,
  `--anim-shimmer`, `--anim-spin`.
- Only `transform` and `opacity` animate. Press feedback scales (buttons 0.98, rows
  0.995, composer buttons 0.94).
- `prefers-reduced-motion: reduce` collapses every animation and transition to 1 ms,
  one iteration; the static state must still read correctly.

## 6. The mark

`frontend/src/ui/Mark.tsx`: a ring in the ink (the foreground at 24%), her orb in
emerald, and two arcs of voice leaving it (85% and 50%). It reads as a presence that
speaks. Sizes: 17 (answer gutter), 22, 26 (sidebar), 34 (empty conversation), 40.
Always `aria-hidden`; its label is elsewhere. Wordmark: the mark and "Veronica" at
1 rem semibold, tracking −0.02em, gap 10 px.

## 7. Themes

- `:root { color-scheme: light dark }` and `light-dark()` everywhere: the system picks
  the half. `data-theme="light|dark"` on any element forces a theme for that subtree
  (the design page's islands rely on it).
- The production CSS target is Chromium 123 (`build.cssTarget` in `vite.config.ts`),
  WebView2's floor for `light-dark()`. Aimed lower, the minifier rewrites `light-dark()`
  into fallbacks that break forced themes on inner elements.
- `styles/base.css` is imported into Tailwind's `base` layer, under its utilities; CSS
  modules are unlayered and override both. No screen uses a Tailwind utility.
- System / Light / Dark is chosen in Settings → General (three small pictures of the app,
  each drawn in its own theme) or from the command palette. `lib/theme.ts` applies it
  to `<html>`, a pre-paint script in `index.html` does the same before the first frame,
  and transitions are paused for the switch so surfaces change together.

## 8. Components

These apply to all of them: one focus ring (`outline: 2px solid var(--ring)`, offset
2 px), never removed; errors are a colour and a sentence, never a colour alone; badges
always carry a label; every async surface has loading (a skeleton shaped like its
content), empty, offline, permission-denied and failed states.

- **Unknown is not empty.** A list that is loading or failed to load shows a skeleton or
  a sentence with Try again, never its empty state and never a count of 0.
- **Long content wraps inside its box.** A grid that holds user text gets
  `minmax(0, 1fr)` tracks, and a name that may be one long word gets
  `overflow-wrap: anywhere`; `body` breaks words that would overflow. Wide tables and
  code scroll inside their own frame. Nothing pushes the page sideways.
- **Writing never waits for the network.** Fields stay editable while the backend is
  away; only the action that needs it (Send, Attach, Start a call) waits, and a line
  says why.
- **Settings** are titled groups on one card, rows split by hairlines: the label and what
  it changes on the left, the control on the right (`settings/Group.tsx`). A switch is
  a row. A control made of cards (the theme pictures) sits without the group card, and so
  does a failure notice. Chosen settings are blue, not emerald.
- **Disclosures** are native `<details>` with a chevron that turns (`ui/Disclosure.tsx`).
- **Server text** reaches the screen only as curated copy from the error contract, never
  a stock status phrase or an exception.

## 9. Anti-patterns

No hard-coded colour, size, radius, shadow or duration in a component; no pure
`#000`/`#FFF`; no emoji in the interface; no bubbles in the conversation; no filled
emerald blocks for selection; no glow except her presence; no animation by keyframe
name inside a module; no animating layout properties; no weights above 600; no hover
styles outside `(hover: hover)`; no text in `--faint-foreground`; no placeholder names
or invented figures in shipped screens (the design page uses made-up people).
