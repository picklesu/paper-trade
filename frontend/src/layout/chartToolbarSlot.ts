import { createContext, useContext } from 'react'

/** Where the chart's toolbar goes: a spot in the chart's tab bar, so the candles get that
 *  row back. `compact` when the chart is too narrow for every button (range and bar size
 *  become drop-downs). Null when there's no room there, and the toolbar stays in the panel. */
export interface ChartToolbarSlot {
  element: HTMLElement
  compact: boolean
}

export const ChartToolbarSlotContext = createContext<ChartToolbarSlot | null>(null)
export const SetChartToolbarSlotContext = createContext<(slot: ChartToolbarSlot | null) => void>(() => {})

export function useChartToolbarSlot(): ChartToolbarSlot | null {
  return useContext(ChartToolbarSlotContext)
}
