import type { DockviewApi } from 'dockview-react'
import { createContext, useContext } from 'react'
import type { PanelId } from './panels'

export interface LayoutControls {
  /** Panels currently open (docked, tabbed or floating). */
  open: Set<PanelId>
  toggle: (id: PanelId) => void
  reset: () => void
}

export const LayoutContext = createContext<LayoutControls | null>(null)

/** Lets the workspace hand its dockview API to LayoutProvider once ready. */
export const ApiSetterContext = createContext<(api: DockviewApi) => void>(() => {})

/** Show/hide panels and reset the layout (null outside LayoutProvider). */
export function useLayout(): LayoutControls | null {
  return useContext(LayoutContext)
}
