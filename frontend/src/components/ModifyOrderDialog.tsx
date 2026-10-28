import { useState } from 'react'
import { replaceOrder, type OrderChange } from '../lib/api'
import { orderTypeLabel } from '../lib/format'
import type { Order, TimeInForce } from '../lib/types'

interface Props {
  order: Order
  onClose: () => void
}

/** Change a working order's quantity, prices or time in force (cancel/replace). */
export function ModifyOrderDialog({ order, onClose }: Props) {
  const [qty, setQty] = useState(String(order.qty))
  const [limit, setLimit] = useState(order.limit_price?.toFixed(2) ?? '')
  const [stop, setStop] = useState(order.stop_price?.toFixed(2) ?? '')
  const [tif, setTif] = useState<TimeInForce>(order.tif)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    const change: OrderChange = { qty: Number(qty), tif }
    if (order.limit_price !== null) change.limit_price = Number(limit)
    if (order.stop_price !== null) change.stop_price = Number(stop)
    setBusy(true)
    setError(null)
    try {
      await replaceOrder(order.id, change)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modify-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="modify-title">
          Modify order #{order.id}: {order.side === 'buy' ? 'Buy' : 'Sell'} {order.symbol}, {orderTypeLabel(order)}
        </h2>
        <label className="field">
          <span>Quantity (shares)</span>
          <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        {order.stop_price !== null && (
          <label className="field">
            <span>Stop price</span>
            <input type="number" min={0.01} step={0.01} value={stop} onChange={(e) => setStop(e.target.value)} />
          </label>
        )}
        {order.limit_price !== null && (
          <label className="field">
            <span>Limit price</span>
            <input type="number" min={0.01} step={0.01} value={limit} onChange={(e) => setLimit(e.target.value)} />
          </label>
        )}
        <label className="field">
          <span>Time in force</span>
          <select value={tif} onChange={(e) => setTif(e.target.value as TimeInForce)}>
            <option value="day">Day (expires at 4:00 PM)</option>
            <option value="gtc">Good till canceled</option>
          </select>
        </label>
        <p className="muted">
          The order is canceled and replaced by a new one with your changes, as at a real broker. If the new order can
          trade right away, it fills.
        </p>
        <div className="dialog-actions">
          <button className="btn primary" disabled={busy} onClick={save}>
            Save changes
          </button>
          <button className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  )
}
