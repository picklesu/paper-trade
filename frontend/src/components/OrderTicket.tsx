import { useEffect, useRef, useState } from 'react'
import { placeOrder } from '../lib/api'
import { dollars, price } from '../lib/format'
import type { Account, Order, OrderType, Quote, Side, TimeInForce } from '../lib/types'

interface Props {
  quote: Quote
  account: Account
}

const TYPES: { value: OrderType; label: string }[] = [
  { value: 'market', label: 'Market' },
  { value: 'limit', label: 'Limit' },
  { value: 'stop', label: 'Stop' },
  { value: 'stop_limit', label: 'Stop limit' },
]

type Result = { ok: true; text: string } | { ok: false; text: string }

function describe(o: Order): string {
  const what = `${o.side === 'buy' ? 'Bought' : 'Sold'} ${o.qty} ${o.symbol}`
  if (o.status === 'filled') return `${what} at ${price(o.fill_price!)}`
  if (o.status === 'rejected') return `Order #${o.id} rejected: ${o.reason}`
  return `Order #${o.id} is working`
}

export function OrderTicket({ quote, account }: Props) {
  const [side, setSide] = useState<Side>('buy')
  const [qty, setQty] = useState('10')
  const [type, setType] = useState<OrderType>('market')
  const [limit, setLimit] = useState('')
  const [stop, setStop] = useState('')
  const [tif, setTif] = useState<TimeInForce>('day')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  // Start price fields at the current price when the symbol or order type changes,
  // but not on every tick (that would overwrite what the user is typing).
  const lastRef = useRef(quote.last)
  useEffect(() => {
    lastRef.current = quote.last
  })
  useEffect(() => {
    setLimit(lastRef.current.toFixed(2))
    setStop(lastRef.current.toFixed(2))
    setResult(null)
  }, [quote.symbol, type])

  const needsLimit = type === 'limit' || type === 'stop_limit'
  const needsStop = type === 'stop' || type === 'stop_limit'
  const shares = Number(qty)
  const owned = account.positions.find((p) => p.symbol === quote.symbol)?.qty ?? 0

  const estPrice = needsLimit ? Number(limit) : needsStop ? Number(stop) : side === 'buy' ? quote.ask : quote.bid
  const estTotal = Number.isFinite(shares * estPrice) ? shares * estPrice : 0

  const submit = async () => {
    setBusy(true)
    setResult(null)
    try {
      const order = await placeOrder({
        symbol: quote.symbol,
        side,
        qty: shares,
        type,
        tif: type === 'market' ? 'day' : tif,
        limit_price: needsLimit ? Number(limit) : undefined,
        stop_price: needsStop ? Number(stop) : undefined,
      })
      setResult({ ok: order.status !== 'rejected', text: describe(order) })
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="ticket-form"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
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
      {type !== 'market' && (
        <label className="field">
          <span>Time in force</span>
          <select value={tif} onChange={(e) => setTif(e.target.value as TimeInForce)}>
            <option value="day">Day (expires at 4:00 PM)</option>
            <option value="gtc">Good till canceled</option>
          </select>
        </label>
      )}

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
        {side === 'buy' ? 'Buy' : 'Sell'} {shares > 0 ? shares.toLocaleString() : ''} {quote.symbol}
      </button>

      {result && (
        <p className={result.ok ? 'ticket-result ok' : 'ticket-result error'} role={result.ok ? 'status' : 'alert'}>
          {result.text}
        </p>
      )}
    </form>
  )
}
