/** Join the class names that are set; `false`, `null` and `undefined` drop out. */
export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(' ')
}
