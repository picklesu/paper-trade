import { createContext, type Dispatch, type SetStateAction, useContext } from 'react'

/** Where the chart's toolbar goes: a spot in the chart's tab bar, so the candles get that
 *  row back. `compact` when the chart is too narrow for every button (range and bar size
 *  become drop-downs). Null in the workspace when there's no room there: the toolbar stays
 *  in the panel, compact so it keeps to one row. Undefined outside the workspace (a chart on
 *  its own): every button, in the panel. */
export interface ChartToolbarSlot {
  element: HTMLElement
  compact: boolean
}

export const ChartToolbarSlotContext = createContext<ChartToolbarSlot | null | undefined>(undefined)
export const SetChartToolbarSlotContext = createContext<Dispatch<SetStateAction<ChartToolbarSlot | null>>>(() => {})

export function useChartToolbarSlot(): ChartToolbarSlot | null | undefined {
  return useContext(ChartToolbarSlotContext)
}
