import type { IDockviewPanelProps } from 'dockview-react'
import { type ReactNode, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { Orders, Positions } from '../components/AccountViews'
import { OptionChain } from '../components/OptionChain'
import { OrderTicket } from '../components/OrderTicket'
import { Placeholder } from '../components/Panel'
import { PriceChart } from '../components/PriceChart'
import { QuoteDetail } from '../components/QuoteDetail'
import { Watchlist } from '../components/Watchlist'
import { fetchExpirations } from '../lib/api'
import { parseOcc, pickExpiration } from '../lib/options'
import { isIndex } from '../lib/symbols'
import type { Expiration } from '../lib/types'
import { useAppData } from './appDataContext'
import { useLayout } from './layoutContext'

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

/** Why the selected symbol has no price: it may not have been listed yet at the market's date. */
function useUnlisted(): string | null {
  const { tick, symbols, selected } = useAppData()
  const from = symbols.find((s) => s.symbol === selected)?.listed_from
  // The market's date (New York), whatever offset the clock string carries.
  const today = tick ? new Date(tick.clock.time).toLocaleDateString('en-CA', { timeZone: 'America/New_York' }) : undefined
  return from && today && from > today ? `${selected} wasn't trading yet on ${today}. Its first trading day was ${from}.` : null
}

function useWaiting(): ReactNode {
  const unlisted = useUnlisted()
  return unlisted ? <Placeholder text={unlisted} /> : waiting
}

export function ChartView() {
  const { tick, quotes, orders, history, selected, setLookup } = useAppData()
  const waiting = useWaiting()
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
            header={<QuoteDetail quote={quote} onLookup={() => setLookup('')} />}
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
  const { tick, quotes, orders, selected, chain, ticketRequest } = useAppData()
  const waiting = useWaiting()
  const quote = quotes.get(selected)
  // A chain click is for the selected symbol; one for another symbol (stale) is ignored.
  const fill = ticketRequest && parseOcc(ticketRequest.symbol)?.underlying === selected ? ticketRequest : null
  return (
    <Frame label="Order Entry">
      {isIndex(selected) ? (
        <Placeholder text={`${selected} is an index: it is quoted and charted, but it can't be traded.`} />
      ) : quote && tick ? <OrderTicket quote={quote} account={tick.account} orders={orders} chain={chain} fill={fill} /> : waiting}
    </Frame>
  )
}

export function OptionsView({ api }: IDockviewPanelProps) {
  const { tick, selected, chain, setChainRequest, requestTicket } = useAppData()
  const layout = useLayout()
  const unlisted = useUnlisted()
  const shown = usePanelShown(api)
  const [wanted, setWanted] = useState<string | null>(null)
  const expirations = useExpirations(selected, tick?.clock.session_open, tick?.clock.timeline, tick?.clock.calibration)
  const expiration = pickExpiration(expirations, wanted)

  // Stream the chain only while the panel is showing (not collapsed to its edge).
  useEffect(() => {
    setChainRequest(shown && expiration ? { symbol: selected, expiration } : null)
  }, [shown, selected, expiration, setChainRequest])
  useEffect(() => () => setChainRequest(null), [setChainRequest])

  const current = chain && chain.symbol === selected && chain.expiration === expiration ? chain : null
  return (
    <Frame label="Options">
      {isIndex(selected) ? (
        <Placeholder text={`${selected} is an index: it has no options.`} />
      ) : unlisted ? (
        <Placeholder text={unlisted} />
      ) : tick ? (
        <OptionChain
          symbol={selected}
          expirations={expirations}
          expiration={expiration}
          onExpiration={setWanted}
          chain={current}
          onTrade={(symbol, side, limit) => {
            requestTicket({ symbol, side, limit })
            layout?.show('ticket')
          }}
        />
      ) : (
        waiting
      )}
    </Frame>
  )
}

/** Whether a panel is showing: dockview hides a collapsed panel's group. */
function usePanelShown(api: IDockviewPanelProps['api']): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      // Follow the group the panel is in now (moving a panel puts it in another).
      let groupSub = api.group?.api.onDidVisibilityChange(onChange)
      const subs = [
        api.onDidVisibilityChange(onChange),
        api.onDidGroupChange(() => {
          groupSub?.dispose()
          groupSub = api.group?.api.onDidVisibilityChange(onChange)
          onChange()
        }),
      ]
      return () => {
        subs.forEach((s) => s.dispose())
        groupSub?.dispose()
      }
    },
    [api],
  )
  return useSyncExternalStore(subscribe, () => api.isVisible && (api.group?.api.isVisible ?? true))
}

/** A symbol's open expirations, again whenever the trading day, the timeline or an
 *  option calibration (which can change the listing pattern) changes. */
function useExpirations(symbol: string, sessionOpen: string | undefined, timeline: number | undefined, calibration: number | undefined): Expiration[] {
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
  }, [symbol, sessionOpen, timeline, calibration])
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
