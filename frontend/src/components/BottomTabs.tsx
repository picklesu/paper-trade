import { useState } from 'react'
import { cancelOrder } from '../lib/api'
import { direction, dollars, marketStamp, orderTypeLabel, percent, price, signedDollars } from '../lib/format'
import type { Account, Order } from '../lib/types'
import { Placeholder } from './Panel'
import { ResetAccountDialog } from './ResetAccountDialog'

interface Props {
  account: Account | null
  orders: Order[]
  onSelect: (symbol: string) => void
}

type TabId = 'positions' | 'orders' | 'activity'

export function BottomTabs({ account, orders, onSelect }: Props) {
  const [active, setActive] = useState<TabId>('positions')
  const [showReset, setShowReset] = useState(false)
  const working = orders.filter((o) => o.status === 'working')
  const fills = orders.filter((o) => o.status === 'filled')

  const tabs: { id: TabId; label: string }[] = [
    { id: 'positions', label: `Positions${account?.positions.length ? ` (${account.positions.length})` : ''}` },
    { id: 'orders', label: `Orders${working.length ? ` (${working.length} working)` : ''}` },
    { id: 'activity', label: 'Activity' },
  ]

  return (
    <section className="panel bottom">
      <div className="tabs" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={t.id === active}
            className={t.id === active ? 'tab active' : 'tab'}
            onClick={() => setActive(t.id)}
          >
            {t.label}
          </button>
        ))}
        <div className="tabs-spacer" />
        {account && (
          <button className="ctl tabs-action" onClick={() => setShowReset(true)}>
            Reset account…
          </button>
        )}
      </div>
      <div className="panel-body" role="tabpanel">
        {active === 'positions' && <Positions account={account} onSelect={onSelect} />}
        {active === 'orders' && <Orders orders={orders} />}
        {active === 'activity' && <Activity fills={fills} />}
      </div>
      {showReset && account && (
        <ResetAccountDialog startingCash={account.starting_cash} onClose={() => setShowReset(false)} />
      )}
    </section>
  )
}

function Positions({ account, onSelect }: { account: Account | null; onSelect: (s: string) => void }) {
  if (!account?.positions.length) return <Placeholder text="No positions. Use the order ticket to buy shares." />
  return (
    <table className="grid-table clickable">
      <thead>
        <tr>
          <th>Symbol</th>
          <th className="num">Qty</th>
          <th className="num">Avg cost</th>
          <th className="num">Last</th>
          <th className="num">Market value</th>
          <th className="num">Unrealized P&L</th>
        </tr>
      </thead>
      <tbody>
        {account.positions.map((p) => (
          <tr key={p.symbol} onClick={() => onSelect(p.symbol)}>
            <td className="sym">{p.symbol}</td>
            <td className="num">{p.qty.toLocaleString()}</td>
            <td className="num">{price(p.avg_cost)}</td>
            <td className="num">{price(p.last)}</td>
            <td className="num">{dollars(p.market_value)}</td>
            <td className={`num ${direction(p.unrealized)}`}>
              {signedDollars(p.unrealized)} ({percent(p.unrealized_pct)})
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const STATUS_LABEL = {
  working: 'Working',
  filled: 'Filled',
  canceled: 'Canceled',
  expired: 'Expired',
  rejected: 'Rejected',
} as const

function Orders({ orders }: { orders: Order[] }) {
  const [error, setError] = useState<string | null>(null)
  if (!orders.length) return <Placeholder text="No orders yet." />
  // Working orders first, then the rest newest first (the server sends newest first).
  const sorted = [...orders.filter((o) => o.status === 'working'), ...orders.filter((o) => o.status !== 'working')]

  const cancel = (id: number) => {
    setError(null)
    cancelOrder(id).catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }

  return (
    <>
      {error && (
        <p className="error inline" role="alert">
          {error}
        </p>
      )}
      <table className="grid-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Placed</th>
            <th>Symbol</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th>Type</th>
            <th>TIF</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((o) => (
            <tr key={o.id}>
              <td className="muted">{o.id}</td>
              <td>{marketStamp(o.created_at)}</td>
              <td className="sym">{o.symbol}</td>
              <td className={o.side === 'buy' ? 'up' : 'down'}>{o.side === 'buy' ? 'Buy' : 'Sell'}</td>
              <td className="num">{o.qty.toLocaleString()}</td>
              <td>{orderTypeLabel(o)}</td>
              <td>{o.tif === 'gtc' ? 'GTC' : 'Day'}</td>
              <td className={`status-${o.status}`} title={o.reason ?? undefined}>
                {STATUS_LABEL[o.status]}
                {o.status === 'filled' && ` at ${price(o.fill_price!)}`}
                {o.status === 'working' && o.triggered_at && ' (stop hit)'}
                {o.reason && ` · ${o.reason}`}
              </td>
              <td>
                {o.status === 'working' && (
                  <button className="ctl" onClick={() => cancel(o.id)} aria-label={`Cancel order ${o.id}`}>
                    Cancel
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function Activity({ fills }: { fills: Order[] }) {
  if (!fills.length) return <Placeholder text="No trades yet." />
  const byTime = [...fills].sort((a, b) => b.filled_at!.localeCompare(a.filled_at!))
  return (
    <table className="grid-table">
      <thead>
        <tr>
          <th>Filled</th>
          <th>Symbol</th>
          <th>Side</th>
          <th className="num">Qty</th>
          <th className="num">Price</th>
          <th className="num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {byTime.map((o) => {
          const amount = o.fill_price! * o.qty
          return (
            <tr key={o.id}>
              <td>{marketStamp(o.filled_at!)}</td>
              <td className="sym">{o.symbol}</td>
              <td className={o.side === 'buy' ? 'up' : 'down'}>{o.side === 'buy' ? 'Bought' : 'Sold'}</td>
              <td className="num">{o.qty.toLocaleString()}</td>
              <td className="num">{price(o.fill_price!)}</td>
              <td className="num">{o.side === 'buy' ? `-${dollars(amount)}` : `+${dollars(amount)}`}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
