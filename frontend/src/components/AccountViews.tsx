/** The account views: positions and orders (each a workspace panel). */
import { useState } from 'react'
import { cancelOrder } from '../lib/api'
import { direction, dollars, marketStamp, orderTypeLabel, percent, price, signedDollars } from '../lib/format'
import type { Account, Order, Position } from '../lib/types'
import { ExerciseDialog } from './ExerciseDialog'
import { statusText, tifLabel } from '../lib/orders'
import { ModifyOrderDialog } from './ModifyOrderDialog'
import { Placeholder } from './Panel'
import { ResetAccountDialog } from './ResetAccountDialog'

export function Positions({ account, onSelect }: { account: Account | null; onSelect: (s: string) => void }) {
  const [showReset, setShowReset] = useState(false)
  return (
    <>
      {account && (
        <div className="panel-toolbar">
          <button className="ctl" onClick={() => setShowReset(true)}>
            Reset account…
          </button>
        </div>
      )}
      <PositionsTable account={account} onSelect={onSelect} />
      {showReset && account && (
        <ResetAccountDialog startingCash={account.starting_cash} onClose={() => setShowReset(false)} />
      )}
    </>
  )
}

function PositionsTable({ account, onSelect }: { account: Account | null; onSelect: (s: string) => void }) {
  const [exercising, setExercising] = useState<Position | null>(null)
  if (!account?.positions.length) return <Placeholder text="No positions. Use the order ticket to buy shares or options." />
  const hasOptions = account.positions.some((p) => p.option)
  // Theta is dollars a day, so it adds up across symbols; delta is in each symbol's shares, so it doesn't.
  const theta = account.positions.reduce((sum, p) => sum + (p.theta ?? 0), 0)
  return (
    <>
      <table className="grid-table clickable">
        <thead>
          <tr>
            <th>Symbol</th>
            <th className="num">Qty</th>
            <th className="num">Avg cost</th>
            <th className="num" title="Options: the mid, halfway between bid and ask">
              Last
            </th>
            <th className="num">Market value</th>
            <th className="num">Unrealized P&L</th>
            {hasOptions && (
              <>
                <th className="num" title="Share-equivalent delta: how many shares the position moves like">
                  Delta
                </th>
                <th className="num" title="Theta: what the position gains or loses in a day from time passing">
                  Theta
                </th>
                <th />
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {account.positions.map((p) => (
            <tr key={p.symbol} onClick={() => onSelect(p.option?.underlying ?? p.symbol)}>
              <td className="sym">{p.option?.label ?? p.symbol}</td>
              <td className="num" title={p.option ? 'Contracts of 100 shares (negative: sold short)' : undefined}>
                {p.qty.toLocaleString()}
              </td>
              <td className="num">{price(p.avg_cost)}</td>
              <td className="num">{price(p.last)}</td>
              <td className="num">{dollars(p.market_value)}</td>
              <td className={`num ${direction(p.unrealized)}`}>
                {signedDollars(p.unrealized)} ({percent(p.unrealized_pct)})
              </td>
              {hasOptions && (
                <>
                  <td className="num">{(p.delta ?? 0).toFixed(0)}</td>
                  <td className={`num ${direction(p.theta ?? 0)}`}>{p.option ? signedDollars(p.theta ?? 0) : ''}</td>
                  <td className="row-actions">
                    {p.option && p.qty > 0 && (
                      <button
                        className="ctl"
                        aria-label={`Exercise ${p.option.label}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          setExercising(p)
                        }}
                      >
                        Exercise…
                      </button>
                    )}
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
        {hasOptions && (
          <tfoot>
            <tr>
              <td colSpan={7}>Total theta{account.collateral ? ` · ${dollars(account.collateral)} set aside for short puts` : ''}</td>
              <td className={`num ${direction(theta)}`}>{signedDollars(theta)}</td>
              <td />
            </tr>
          </tfoot>
        )}
      </table>
      {exercising?.option && <ExerciseDialog position={exercising} onClose={() => setExercising(null)} />}
    </>
  )
}

const FILTERS = [
  { id: 'all', label: 'All', match: () => true },
  { id: 'working', label: 'Working', match: (o: Order) => o.status === 'working' },
  { id: 'filled', label: 'Filled', match: (o: Order) => o.status === 'filled' },
  { id: 'closed', label: 'Canceled / expired / rejected', match: (o: Order) => ['canceled', 'expired', 'rejected'].includes(o.status) },
] as const

type FilterId = (typeof FILTERS)[number]['id']

/** When the order last changed: filled, closed, stop hit, or placed. */
function updatedAt(o: Order): string {
  return o.filled_at ?? o.closed_at ?? o.triggered_at ?? o.created_at
}

export function Orders({ orders }: { orders: Order[] }) {
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterId>('all')
  const [modifying, setModifying] = useState<number | null>(null)
  const modifyingOrder = modifying === null ? undefined : orders.find((o) => o.id === modifying)
  if (!orders.length) return <Placeholder text="No orders yet." />

  const match = FILTERS.find((f) => f.id === filter)!.match
  // Working orders first, then the rest newest first (the server sends newest placed first).
  // Filled lists the trades in the order they filled, latest first: a GTC order placed
  // earlier can fill after later ones.
  const shown =
    filter === 'filled'
      ? orders.filter(match).sort((a, b) => b.filled_at!.localeCompare(a.filled_at!) || b.id - a.id)
      : [...orders.filter((o) => o.status === 'working'), ...orders.filter((o) => o.status !== 'working')].filter(match)

  // The total of the sells shown (buys realize nothing).
  const sells = shown.filter((o) => o.realized != null)
  const realizedTotal = sells.length ? sells.reduce((sum, o) => sum + o.realized!, 0) : null

  const cancel = (id: number) => {
    setError(null)
    cancelOrder(id).catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }

  return (
    <>
      <div className="filters" role="group" aria-label="Show orders">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={f.id === filter ? 'ctl active' : 'ctl'}
            aria-pressed={f.id === filter}
            onClick={() => setFilter(f.id)}
          >
            {f.label} ({orders.filter(f.match).length})
          </button>
        ))}
      </div>
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
            <th className="num" title="Profit or loss a closing trade (a sell, or buying back an option sold) realized against your average price">
              P/L
            </th>
            <th>Updated</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {shown.map((o) => (
            <tr key={o.id}>
              <td className="muted">{o.id}</td>
              <td>{marketStamp(o.created_at)}</td>
              <td className="sym">{o.option?.label ?? o.symbol}</td>
              <td className={o.side === 'buy' ? 'up' : 'down'}>{sideText(o)}</td>
              <td className="num">{o.qty.toLocaleString()}</td>
              <td>{orderTypeLabel(o)}</td>
              <td>{tifLabel(o.tif, true)}</td>
              <td className={`status-${o.status}`} title={o.reason ?? undefined}>
                {statusText(o)}
                {o.replaces !== null && <span className="muted"> · replaces #{o.replaces}</span>}
              </td>
              <PnlCell order={o} />
              <td className="muted">{marketStamp(updatedAt(o))}</td>
              <td className="row-actions">
                {o.status === 'working' && (
                  <>
                    <button className="ctl" onClick={() => setModifying(o.id)} aria-label={`Modify order ${o.id}`}>
                      Modify
                    </button>
                    <button className="ctl" onClick={() => cancel(o.id)} aria-label={`Cancel order ${o.id}`}>
                      Cancel
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
        {realizedTotal !== null && (
          <tfoot>
            <tr>
              <td colSpan={8}>Realized P/L of the closing trades shown</td>
              <td className={`num ${direction(realizedTotal)}`}>{signedDollars(realizedTotal)}</td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        )}
      </table>
      {!shown.length && <Placeholder text="No orders match this filter." />}
      {modifyingOrder && (
        // The live order lets the dialog notice a fill or cancel meanwhile; if going back
        // in time erases the order, the dialog simply goes away.
        <ModifyOrderDialog key={modifyingOrder.id} order={modifyingOrder} onClose={() => setModifying(null)} />
      )}
    </>
  )
}

/** "Buy", "Sell", or for options "Buy to open", "Sell to close"... */
function sideText(o: Order): string {
  if (o.type === 'dividend') return 'Dividend'
  const verb = o.side === 'buy' ? 'Buy' : 'Sell'
  return o.option && o.effect ? `${verb} to ${o.effect}` : verb
}

/** What a filled order made or lost. A sell realizes P/L against the average cost; a buy only
 *  opens (or adds to) a position, whose open P/L is in Positions. */
function PnlCell({ order }: { order: Order }) {
  if (order.type === 'dividend') {
    const paid = order.qty * order.fill_price!
    return <td className={`num ${direction(paid)}`}>{signedDollars(paid)}</td>
  }
  // `!= null`: a server from before `realized` sends none.
  if (order.realized != null) return <td className={`num ${direction(order.realized)}`}>{signedDollars(order.realized)}</td>
  if (order.status === 'filled')
    return (
      <td className="num muted" title="A buy opens a position: its P/L is realized when you sell (open P/L is in Positions)">
        —
      </td>
    )
  return <td />
}
