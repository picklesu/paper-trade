import { createContext, useContext } from 'react'
import type { ChainRequest, useStream } from '../lib/stream'
import type { QuoteStats, Side, SymbolInfo, Watchlist } from '../lib/types'

/** Fill the order ticket (from a click in the option chain). `id` tells repeat clicks apart. */
export interface TicketRequest {
  /** An option's OCC symbol. */
  symbol: string
  /** null (a mid click): keep the ticket's side. */
  side: Side | null
  limit: number
  id: number
}

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
  /** Stream this option chain (null: none, e.g. the Options panel is collapsed). */
  setChainRequest: (request: ChainRequest | null) => void
  ticketRequest: TicketRequest | null
  requestTicket: (request: Omit<TicketRequest, 'id'>) => void
}

export const AppDataContext = createContext<AppData | null>(null)

/** Live market and account data plus the selected symbol (see AppDataProvider). */
export function useAppData(): AppData {
  const data = useContext(AppDataContext)
  if (!data) throw new Error('useAppData outside AppDataProvider')
  return data
}
