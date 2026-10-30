import { type ReactNode, useMemo } from 'react'
import { Orders, Positions } from '../components/AccountViews'
import { OrderTicket } from '../components/OrderTicket'
import { Placeholder } from '../components/Panel'
import { PriceChart } from '../components/PriceChart'
import { QuoteDetail } from '../components/QuoteDetail'
import { Watchlist } from '../components/Watchlist'
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
