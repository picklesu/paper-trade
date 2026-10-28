import type { DockviewApi } from 'dockview-react'
import { createContext, useContext } from 'react'
import type { Edge } from './edges'
import type { PanelId, PanelPlace } from './panels'

export interface LayoutControls {
  /** Where each panel is: open or collapsed on an edge, or off (with the edge it comes back on). */
  places: Record<PanelId, PanelPlace>
  /** Turn a panel on (it opens on its edge) or off. */
  setOn: (id: PanelId, on: boolean) => void
  /** Move a panel to an edge, keeping it open or collapsed (for an off panel, where it comes back). */
  setEdge: (id: PanelId, edge: Edge) => void
  reset: () => void
}

export const LayoutContext = createContext<LayoutControls | null>(null)

/** Lets the workspace hand its dockview API to LayoutProvider once ready. */
export const ApiSetterContext = createContext<(api: DockviewApi) => void>(() => {})

/** The Panels menu's controls (null outside LayoutProvider). */
export function useLayout(): LayoutControls | null {
  return useContext(LayoutContext)
}
