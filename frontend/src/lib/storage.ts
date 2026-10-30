import { useCallback, useState } from 'react'

/** State saved in localStorage: a per-browser convenience, so storage may be
 *  unavailable and saved values may be stale or malformed (`sanitize` decides). */
export function useStoredState<T>(key: string, fallback: T, sanitize: (raw: unknown) => T | null) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw === null ? fallback : (sanitize(JSON.parse(raw)) ?? fallback)
    } catch {
      return fallback
    }
  })
  const set = useCallback(
    (next: T) => {
      setValue(next)
      try {
        localStorage.setItem(key, JSON.stringify(next))
      } catch {
        // Not saved; the choice still applies until the page reloads.
      }
    },
    [key],
  )
  return [value, set] as const
}
