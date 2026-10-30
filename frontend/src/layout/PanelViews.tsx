import type { IDockviewPanelProps } from 'dockview-react'
import { type ReactNode, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { Orders, Positions } from '../components/AccountViews'
import { OptionChain } from '../components/OptionChain'
import { OrderTicket } from '../components/OrderTicket'
import { Placeholder } from '../components/Panel'
import { PriceChart } from '../components/PriceChart'
import { QuoteDetail } from '../components/QuoteDetail'
import { Watchlist } from '../components/Watchlist'
import { fetchExpirations } from '../lib/api'
import { pickExpiration } from '../lib/options'
import type { Expiration } from '../lib/types'
import { useAppData } from './appDataContext'

// The workspace panels. Each fills its dockview panel and scrolls on its own.

// dockview already exposes each group as a region named after its tab, so the
// content gets a plain marker rather than a second landmark.
function Frame({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="dock-panel" data-panel={label}>
      {children}
    </div>
  )
}

const waiting = <Placeholder text="Waiting for market data…" />

export function ChartView() {
  const { tick, quotes, stats, orders, history, selected, setLookup } = useAppData()
  const quote = quotes.get(selected)
  return (
    <Frame label="Chart">
      {quote && tick ? (
        <div className="chart-panel">
          <PriceChart
            quote={quote}
            clock={tick.clock}
            orders={orders}
            history={history}
            header={<QuoteDetail quote={quote} stats={stats[selected]} onLookup={() => setLookup('')} />}
          />
        </div>
      ) : (
        waiting
      )}
    </Frame>
  )
}

export function WatchlistView() {
  const { tick, quotes, stats, watchlists, activeList, setActiveListId, symbols, selected, setSelected } = useAppData()
  const positions = tick?.account.positions
  const held = useMemo(() => new Map((positions ?? []).map((p) => [p.symbol, p.qty])), [positions])
  return (
    <Frame label="Watchlist">
      {tick && watchlists && activeList ? (
        <Watchlist
          lists={watchlists}
          list={activeList}
          onPickList={setActiveListId}
          quotes={quotes}
          stats={stats}
          held={held}
          symbols={symbols}
          clock={tick.clock}
          selected={selected}
          onSelect={setSelected}
        />
      ) : (
        waiting
      )}
    </Frame>
  )
}

export function TicketView() {
  const { tick, quotes, orders, selected } = useAppData()
  const quote = quotes.get(selected)
  return <Frame label="Order Entry">{quote && tick ? <OrderTicket quote={quote} account={tick.account} orders={orders} /> : waiting}</Frame>
}

export function OptionsView({ api }: IDockviewPanelProps) {
  const { tick, selected, chain, setChainRequest } = useAppData()
  const shown = usePanelShown(api)
  const [wanted, setWanted] = useState<string | null>(null)
  const expirations = useExpirations(selected, tick?.clock.session_open, tick?.clock.timeline)
  const expiration = pickExpiration(expirations, wanted)

  // Stream the chain only while the panel is showing (not collapsed to its edge).
  useEffect(() => {
    setChainRequest(shown && expiration ? { symbol: selected, expiration } : null)
  }, [shown, selected, expiration, setChainRequest])
  useEffect(() => () => setChainRequest(null), [setChainRequest])

  const current = chain && chain.symbol === selected && chain.expiration === expiration ? chain : null
  return (
    <Frame label="Options">
      {tick ? (
        <OptionChain symbol={selected} expirations={expirations} expiration={expiration} onExpiration={setWanted} chain={current} />
      ) : (
        waiting
      )}
    </Frame>
  )
}

/** Whether a panel is showing: dockview hides a collapsed panel's group. */
function usePanelShown(api: IDockviewPanelProps['api']): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const subs = [api.onDidVisibilityChange(onChange), api.group?.api.onDidVisibilityChange(onChange)]
      return () => subs.forEach((s) => s?.dispose())
    },
    () => api.isVisible && (api.group?.api.isVisible ?? true),
  )
}

/** A symbol's open expirations, again whenever the trading day or the timeline changes. */
function useExpirations(symbol: string, sessionOpen: string | undefined, timeline: number | undefined): Expiration[] {
  const [loaded, setLoaded] = useState<{ symbol: string; list: Expiration[] }>({ symbol: '', list: [] })
  useEffect(() => {
    if (sessionOpen === undefined) return
    let live = true
    fetchExpirations(symbol)
      .then((list) => live && setLoaded({ symbol, list }))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [symbol, sessionOpen, timeline])
  return loaded.symbol === symbol ? loaded.list : []
}

export function PositionsView() {
  const { tick, setSelected } = useAppData()
  return (
    <Frame label="Positions">
      <Positions account={tick?.account ?? null} onSelect={setSelected} />
    </Frame>
  )
}

export function OrdersView() {
  return (
    <Frame label="Orders">
      <Orders orders={useAppData().orders} />
    </Frame>
  )
}
