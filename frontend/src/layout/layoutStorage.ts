import type { SerializedDockview } from 'dockview-react'

// Bump when panel ids or the default arrangement change incompatibly: old saved
// layouts are then ignored instead of half-applied. (v2: no tab bars, one panel per group,
// side panels collapsed to the left by default.)
const KEY = 'paper-trade.layout.v2'

/** The saved layout for this browser, if any (storage can be unavailable). */
export function loadLayout(): SerializedDockview | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as SerializedDockview) : null
  } catch {
    return null
  }
}

export function saveLayout(layout: SerializedDockview): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(layout))
  } catch {
    // Not saved; the layout still works until the page reloads.
  }
}

export function clearLayout(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // Nothing to clear.
  }
}
