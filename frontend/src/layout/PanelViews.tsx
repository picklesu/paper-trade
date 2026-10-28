import type { ReactNode } from 'react'
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
  const { tick, orders, history, selected } = useAppData()
  const quote = tick?.quotes.find((q) => q.symbol === selected)
  return (
    <Frame label="Chart">
      {quote && tick ? (
        <div className="chart-panel">
          <PriceChart quote={quote} clock={tick.clock} orders={orders} history={history} header={<QuoteDetail quote={quote} />} />
        </div>
      ) : (
        waiting
      )}
    </Frame>
  )
}

/** Holds the chart's place in the grid while it floats (see `CHART_SPACE`). */
export function ChartSpaceView() {
  return (
    <div className="chart-space">
      <p>The chart is floating. To dock it again: Panels ▾ → Chart → Open.</p>
    </div>
  )
}

export function WatchlistView() {
  const { tick, selected, setSelected } = useAppData()
  return (
    <Frame label="Watchlist">{tick?.quotes.length ? <Watchlist quotes={tick.quotes} selected={selected} onSelect={setSelected} /> : waiting}</Frame>
  )
}

export function TicketView() {
  const { tick, orders, selected } = useAppData()
  const quote = tick?.quotes.find((q) => q.symbol === selected)
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
