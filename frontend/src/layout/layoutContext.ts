import type { DockviewApi } from 'dockview-react'
import { createContext, useContext } from 'react'
import type { PanelId, PanelState } from './panels'

export interface LayoutControls {
  /** How each panel is shown: open (docked), collapsed to the edge strip, floating, or off. */
  states: Record<PanelId, PanelState>
  setState: (id: PanelId, state: PanelState) => void
  reset: () => void
}

export const LayoutContext = createContext<LayoutControls | null>(null)

/** Lets the workspace hand its dockview API to LayoutProvider once ready. */
export const ApiSetterContext = createContext<(api: DockviewApi) => void>(() => {})

/** The Panels menu's controls (null outside LayoutProvider). */
export function useLayout(): LayoutControls | null {
  return useContext(LayoutContext)
}
