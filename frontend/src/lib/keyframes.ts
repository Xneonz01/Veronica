// Finds animations that name keyframes the stylesheet never defines. CSS Modules
// renames an animation name written inside a module, so `animation: app-pop-in`
// in a module points at keyframes that don't exist, and the animation silently
// never runs. The build runs this over its own output
// (scripts/check-animations.ts), where every name is final.

const KEYWORDS = new Set([
  'none',
  'initial',
  'inherit',
  'unset',
  'revert',
  'revert-layer',
  'linear',
  'ease',
  'ease-in',
  'ease-out',
  'ease-in-out',
  'step-start',
  'step-end',
  'infinite',
  'normal',
  'reverse',
  'alternate',
  'alternate-reverse',
  'forwards',
  'backwards',
  'both',
  'running',
  'paused',
])

// `animation`, `animation-name`, and the --anim-* tokens that hold a whole
// animation. Other animation-* longhands name no keyframes.
const DECLARATION = /(?:^|[{;\s])(?:animation(?:-name)?|--anim-[\w-]+)\s*:\s*([^;}]+)/g
const KEYFRAMES = /@keyframes\s+([\w-]+)/g
const IDENTIFIER = /^-?[A-Za-z_][\w-]*$/

/** Every animation name used in `css` that no @keyframes in it defines, sorted. */
export function findMissingKeyframes(css: string): string[] {
  const defined = new Set(Array.from(css.matchAll(KEYFRAMES), (match) => match[1]))
  const missing = new Set<string>()
  for (const name of animationNames(css)) {
    if (!defined.has(name)) missing.add(name)
  }
  return [...missing].sort()
}

function animationNames(css: string): string[] {
  const names: string[] = []
  for (const [, value] of css.matchAll(DECLARATION)) {
    for (const animation of splitTopLevel(value, ',')) {
      // A value that is only var(--anim-…) names nothing itself; the token it
      // points at is checked where it is declared.
      const name = splitTopLevel(animation, ' ').find(isName)
      if (name) names.push(name)
    }
  }
  return names
}

function isName(token: string): boolean {
  return IDENTIFIER.test(token) && !KEYWORDS.has(token)
}

/** Splits on a separator outside parentheses: commas inside cubic-bezier() stay put. */
function splitTopLevel(value: string, separator: ',' | ' '): string[] {
  const parts: string[] = []
  let depth = 0
  let current = ''
  for (const char of value) {
    if (char === '(') depth += 1
    else if (char === ')') depth -= 1
    const splits = separator === ' ' ? /\s/.test(char) : char === separator
    if (splits && depth === 0) {
      parts.push(current)
      current = ''
    } else {
      current += char
    }
  }
  parts.push(current)
  return parts.map((part) => part.trim()).filter(Boolean)
}
