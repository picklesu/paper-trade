import { createContext, useContext } from 'react'
import type { useStream } from '../lib/stream'
import type { QuoteStats, SymbolInfo, Watchlist } from '../lib/types'

export type AppData = Omit<ReturnType<typeof useStream>, 'subscribe'> & {
  /** The watchlist shown (the first list until one is picked; null until lists arrive). */
  activeList: Watchlist | null
  setActiveListId: (id: number) => void
  symbols: SymbolInfo[]
  stats: Record<string, QuoteStats>
  selected: string
  setSelected: (symbol: string) => void
  /** The symbol lookup's starting text while it's open; null when closed. */
  lookup: string | null
  setLookup: (text: string | null) => void
}

export const AppDataContext = createContext<AppData | null>(null)

/** Live market and account data plus the selected symbol (see AppDataProvider). */
export function useAppData(): AppData {
  const data = useContext(AppDataContext)
  if (!data) throw new Error('useAppData outside AppDataProvider')
  return data
}
