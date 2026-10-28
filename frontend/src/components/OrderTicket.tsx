import { useEffect, useRef, useState } from 'react'
import { placeOrder, previewOrder, type OrderRequest } from '../lib/api'
import { dollars, price } from '../lib/format'
import type { Account, Order, OrderPreview, OrderType, Quote, Side, TimeInForce } from '../lib/types'
import { OrderReview } from './OrderReview'
import { OrderStatusLine } from './OrderStatusLine'
import { TifSelect } from './TifSelect'

interface Props {
  quote: Quote
  account: Account
  orders: Order[]
}

const TYPES: { value: OrderType; label: string }[] = [
  { value: 'market', label: 'Market' },
  { value: 'limit', label: 'Limit' },
  { value: 'stop', label: 'Stop' },
  { value: 'stop_limit', label: 'Stop limit' },
]

/** Order entry: fill in the form, review the server's preview, then place. */
export function OrderTicket({ quote, account, orders }: Props) {
  const [side, setSide] = useState<Side>('buy')
  const [qty, setQty] = useState('10')
  const [type, setType] = useState<OrderType>('market')
  const [limit, setLimit] = useState('')
  const [stop, setStop] = useState('')
  const [tif, setTif] = useState<TimeInForce>('day')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [review, setReview] = useState<OrderPreview | null>(null)
  const [placed, setPlaced] = useState<Order | null>(null)
  const [placedSeen, setPlacedSeen] = useState(false)

  // Once the stream has shown the placed order, its disappearing means going back in
  // time erased it: drop the line rather than show a stale or reused order.
  const inStream = placed !== null && orders.some((o) => o.id === placed.id)
  if (inStream && !placedSeen) setPlacedSeen(true)
  if (placed && placedSeen && !inStream) {
    setPlaced(null)
    setPlacedSeen(false)
  }

  // Start price fields at the current price when the symbol or order type changes,
  // but not on every tick (that would overwrite what the user is typing).
  const lastRef = useRef(quote.last)
  useEffect(() => {
    lastRef.current = quote.last
  })
  useEffect(() => {
    setLimit(lastRef.current.toFixed(2))
    setStop(lastRef.current.toFixed(2))
    setError(null)
    setReview(null)
  }, [quote.symbol, type])

  const needsLimit = type === 'limit' || type === 'stop_limit'
  const needsStop = type === 'stop' || type === 'stop_limit'
  const shares = Number(qty)
  const owned = account.positions.find((p) => p.symbol === quote.symbol)?.qty ?? 0

  const estPrice = needsLimit ? Number(limit) : needsStop ? Number(stop) : side === 'buy' ? quote.ask : quote.bid
  const estTotal = Number.isFinite(shares * estPrice) ? shares * estPrice : 0

  const request = (): OrderRequest => ({
    symbol: quote.symbol,
    side,
    qty: shares,
    type,
    tif: type === 'market' ? 'day' : tif,
    limit_price: needsLimit ? Number(limit) : undefined,
    stop_price: needsStop ? Number(stop) : undefined,
  })

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const reviewOrder = () =>
    run(async () => {
      setReview(await previewOrder(request()))
    })

  const place = () =>
    run(async () => {
      const order = await placeOrder(request())
      setPlaced(order)
      setPlacedSeen(false)
      setReview(null)
    })

  if (review) {
    return (
      <div className="ticket-form">
        <OrderReview preview={review} busy={busy} onPlace={place} onEdit={() => setReview(null)} />
        {error && (
          <p className="ticket-result error" role="alert">
            {error}
          </p>
        )}
      </div>
    )
  }

  return (
    <form
      className="ticket-form"
      onSubmit={(e) => {
        e.preventDefault()
        reviewOrder()
      }}
    >
      <div className="ticket-quote">
        <span className="sym">{quote.symbol}</span>
        <span className="muted">
          Bid {price(quote.bid)} · Ask {price(quote.ask)}
        </span>
      </div>

      <div className="side-toggle" role="group" aria-label="Side">
        {(['buy', 'sell'] as const).map((s) => (
          <button
            key={s}
            type="button"
            className={side === s ? `side ${s} active` : `side ${s}`}
            aria-pressed={side === s}
            onClick={() => setSide(s)}
          >
            {s === 'buy' ? 'Buy' : 'Sell'}
          </button>
        ))}
      </div>

      <label className="field">
        <span>Quantity (shares)</span>
        <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
      </label>

      <label className="field">
        <span>Order type</span>
        <select value={type} onChange={(e) => setType(e.target.value as OrderType)}>
          {TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </label>

      {needsStop && (
        <label className="field">
          <span>Stop price</span>
          <input type="number" min={0.01} step={0.01} value={stop} onChange={(e) => setStop(e.target.value)} />
        </label>
      )}
      {needsLimit && (
        <label className="field">
          <span>Limit price</span>
          <input type="number" min={0.01} step={0.01} value={limit} onChange={(e) => setLimit(e.target.value)} />
        </label>
      )}
      {type !== 'market' && <TifSelect value={tif} onChange={setTif} />}

      <dl className="ticket-summary">
        <div>
          <dt>Estimated {side === 'buy' ? 'cost' : 'proceeds'}</dt>
          <dd>{dollars(estTotal)}</dd>
        </div>
        <div>
          <dt>{side === 'buy' ? 'Buying power' : 'Shares owned'}</dt>
          <dd>{side === 'buy' ? dollars(account.buying_power) : owned.toLocaleString()}</dd>
        </div>
      </dl>

      <button type="submit" className={`btn submit ${side}`} disabled={busy || !(shares > 0)}>
        Review {side === 'buy' ? 'buy' : 'sell'} {shares > 0 ? shares.toLocaleString() : ''} {quote.symbol}
      </button>

      {error && (
        <p className="ticket-result error" role="alert">
          {error}
        </p>
      )}
      {placed && !error && <OrderStatusLine order={placed} orders={orders} />}
    </form>
  )
}
