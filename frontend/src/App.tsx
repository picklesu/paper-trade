import { useState } from 'react'
import { BottomTabs } from './components/BottomTabs'
import { OrderTicket } from './components/OrderTicket'
import { Panel, Placeholder } from './components/Panel'
import { QuoteDetail } from './components/QuoteDetail'
import { TopBar } from './components/TopBar'
import { Watchlist } from './components/Watchlist'
import { useStream } from './lib/stream'

export default function App() {
  const { status, tick, progress, orders } = useStream()
  const [selected, setSelected] = useState('SPY')
  const quotes = tick?.quotes ?? []
  const quote = quotes.find((q) => q.symbol === selected)

  return (
    <div className="app">
      <TopBar
        status={status}
        clock={tick?.clock ?? null}
        regime={tick?.regime ?? null}
        progress={progress}
        account={tick?.account ?? null}
      />
      <main className="workspace">
        <Panel title="Watchlist" className="watchlist">
          {quotes.length ? (
            <Watchlist quotes={quotes} selected={selected} onSelect={setSelected} />
          ) : (
            <Placeholder text="Waiting for market data…" />
          )}
        </Panel>
        <Panel title="Chart" className="chart">
          {quote ? <QuoteDetail quote={quote} /> : <Placeholder text="Waiting for market data…" />}
        </Panel>
        <Panel title="Order Entry" className="ticket">
          {quote && tick ? (
            <OrderTicket quote={quote} account={tick.account} />
          ) : (
            <Placeholder text="Waiting for market data…" />
          )}
        </Panel>
        <BottomTabs account={tick?.account ?? null} orders={orders} onSelect={setSelected} />
      </main>
    </div>
  )
}
