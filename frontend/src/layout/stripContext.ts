import { createContext, useContext } from 'react'
import type { Edge } from './edges'

/** A panel's tab on its edge strip. */
export interface StripTab {
  groupId: string
  panelId: string
  title: string
  edge: Edge
  /** Open (docked and shown) rather than collapsed to the tab. */
  open: boolean
}

export interface StripControls {
  /** Every panel on an edge, in their usual order. */
  tabs: StripTab[]
  /** Open a collapsed panel, or collapse an open one. */
  toggle: (groupId: string) => void
}

export const StripContext = createContext<StripControls | null>(null)

export function useStrips(): StripControls | null {
  return useContext(StripContext)
}
