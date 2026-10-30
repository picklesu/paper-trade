import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { fetchQuoteStats, fetchSymbols } from '../lib/api'
import { useStoredState } from '../lib/storage'
import { type ChainRequest, useStream } from '../lib/stream'
import type { QuoteStats, SymbolInfo } from '../lib/types'
import { AppDataContext } from './appDataContext'

const ACTIVE_LIST_KEY = 'paper-trade.watchlist.active'

/** Live market and account data plus the selected symbol, shared by every panel. */
export function AppDataProvider({ children }: { children: ReactNode }) {
  const { status, tick, quotes, progress, orders, history, watchlists, chain, subscribe } = useStream()
  const [selected, setSelected] = useState('SPY')
  const [activeListId, setActiveListId] = useStoredState<number | null>(ACTIVE_LIST_KEY, null, (v) =>
    typeof v === 'number' ? v : null,
  )
  // The option chain the Options panel shows while it's open.
  const [chainRequest, setChainRequest] = useState<ChainRequest | null>(null)
  // The symbol lookup's text while it's open (null: closed).
  const [lookup, setLookup] = useState<string | null>(null)
  const symbols = useSymbols()
  const stats = useQuoteStats(tick?.clock.session_open, tick?.clock.timeline)

  const activeList = watchlists?.find((w) => w.id === activeListId) ?? watchlists?.[0] ?? null

  // Only what's on screen: the watchlist's symbols, the selected one and those held, and
  // the option chain if the Options panel is open.
  const held = tick?.account.positions.map((p) => p.symbol).join(',') ?? ''
  const listSymbols = activeList?.symbols.join(',')
  const chainSymbol = chainRequest?.symbol
  const chainExpiration = chainRequest?.expiration
  useEffect(() => {
    if (listSymbols === undefined) return // lists not loaded yet: keep receiving everything
    const wanted = [...listSymbols.split(','), selected, ...held.split(',')].filter(Boolean)
    const chain = chainSymbol && chainExpiration ? { symbol: chainSymbol, expiration: chainExpiration } : null
    subscribe([...new Set(wanted)].sort(), chain)
  }, [listSymbols, selected, held, chainSymbol, chainExpiration, subscribe])

  const value = useMemo(
    () => ({
      status,
      tick,
      quotes,
      progress,
      orders,
      history,
      watchlists,
      activeList,
      setActiveListId,
      symbols,
      stats,
      selected,
      setSelected,
      lookup,
      setLookup,
      chain,
      setChainRequest,
    }),
    [status, tick, quotes, progress, orders, history, watchlists, activeList, setActiveListId, symbols, stats, selected, lookup, chain],
  )
  return <AppDataContext.Provider value={value}>{children}</AppDataContext.Provider>
}

/** Every tradable symbol, for lookups (fetched once; retried while it fails). */
function useSymbols(): SymbolInfo[] {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([])
  useEffect(() => {
    let live = true
    let retry: ReturnType<typeof setTimeout> | undefined
    const load = () =>
      fetchSymbols()
        .then((list) => live && setSymbols(list))
        .catch(() => {
          if (live) retry = setTimeout(load, 5000)
        })
    load()
    return () => {
      live = false
      clearTimeout(retry)
    }
  }, [])
  return symbols
}

/** 52-week ranges and average volumes: they change with the trading day or the history. */
function useQuoteStats(sessionOpen: string | undefined, timeline: number | undefined): Record<string, QuoteStats> {
  const [stats, setStats] = useState<Record<string, QuoteStats>>({})
  useEffect(() => {
    if (sessionOpen === undefined) return
    let live = true
    fetchQuoteStats()
      .then((s) => live && setStats(s))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [sessionOpen, timeline])
  return stats
}
