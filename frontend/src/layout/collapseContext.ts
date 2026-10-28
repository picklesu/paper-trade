import type { DockviewGroupPanel } from 'dockview-react'
import { createContext, useContext } from 'react'
import type { Edge } from './edges'

/** A docked group shown as a strip of tabs on a workspace edge. */
export interface CollapsedGroup {
  id: string
  edge: Edge
  panels: { id: string; title: string }[]
  activePanel: string | undefined
}

export interface CollapseControls {
  /** Collapsed groups, including the one being peeked at. */
  collapsed: CollapsedGroup[]
  /** The collapsed group shown for now (it collapses again on a click elsewhere), if any. */
  peeking: string | null
  collapse: (group: DockviewGroupPanel) => void
  /** Show a collapsed group for now, with this panel in front; again collapses it. */
  peek: (groupId: string, panelId: string) => void
  /** Keep the peeked group open. */
  pin: () => void
}

export const CollapseContext = createContext<CollapseControls | null>(null)

/** Lets the workspace hand its collapse controls to LayoutProvider, for the Panels menu. */
export const CollapseSetterContext = createContext<(controls: CollapseControls | null) => void>(() => {})

export function useCollapse(): CollapseControls | null {
  return useContext(CollapseContext)
}
