import type { SerializedDockview } from 'dockview-react'

// Bump when panel ids or the default arrangement change incompatibly: old saved
// layouts are then ignored instead of half-applied. (v2: no tab bars, one panel per group,
// side panels collapsed to the left by default.)
const KEY = 'paper-trade.layout.v2'

/** The saved layout for this browser, if any (storage can be unavailable), without panels
 *  that no longer exist (`known` are the component names; e.g. Activity was removed). */
export function loadLayout(known: ReadonlySet<string>): SerializedDockview | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? dropUnknownPanels(JSON.parse(raw) as SerializedDockview, known) : null
  } catch {
    return null
  }
}

type Node = SerializedDockview['grid']['root']
type Leaf = { id: string; views: string[]; activeView?: string }

function dropUnknownPanels(layout: SerializedDockview, known: ReadonlySet<string>): SerializedDockview {
  const gone = Object.entries(layout.panels ?? {})
    .filter(([, p]) => !known.has((p as { contentComponent?: string }).contentComponent ?? ''))
    .map(([id]) => id)
  if (!gone.length) return layout
  const keep = (data: Leaf): Leaf | null => {
    const views = data.views.filter((v) => !gone.includes(v))
    if (!views.length) return null
    return { ...data, views, activeView: data.activeView && views.includes(data.activeView) ? data.activeView : views[0] }
  }
  const prune = (node: Node): Node | null => {
    if (node.type === 'leaf') {
      const data = keep(node.data as Leaf)
      return data ? { ...node, data: data as Node['data'] } : null
    }
    const children = (node.data as Node[]).map(prune).filter((c): c is Node => c !== null)
    return children.length ? { ...node, data: children } : null
  }
  const panels = Object.fromEntries(Object.entries(layout.panels).filter(([id]) => !gone.includes(id)))
  const root = prune(layout.grid.root) ?? { type: 'branch', data: [] }
  const floatingGroups = layout.floatingGroups
    ?.map((f) => {
      const data = keep(f.data as unknown as Leaf)
      return data ? { ...f, data: data as unknown as typeof f.data } : null
    })
    .filter((f): f is NonNullable<typeof f> => f !== null)
  return { ...layout, panels, grid: { ...layout.grid, root }, ...(floatingGroups ? { floatingGroups } : {}) }
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
