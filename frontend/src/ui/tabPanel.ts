// The props that make an element the panel of a tab in Tabs.tsx, tied to its tab
// by the same id prefix.

export function panelProps(id: string, value: string) {
  return {
    role: 'tabpanel' as const,
    id: `${id}-panel-${value}`,
    'aria-labelledby': `${id}-tab-${value}`,
    tabIndex: 0,
  }
}
