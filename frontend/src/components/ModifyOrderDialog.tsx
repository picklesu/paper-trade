import { useState } from 'react'
import { Modal } from './Modal'
import { replaceOrder, type OrderChange } from '../lib/api'
import { orderTypeLabel } from '../lib/format'
import { statusText } from '../lib/orders'
import type { Order, TimeInForce } from '../lib/types'
import { TifSelect } from './TifSelect'

interface Props {
  /** The live order from the stream, so the dialog notices if it fills or closes meanwhile. */
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
  // Once a stop has triggered, the order works as a limit; only the limit can change.
  const stopEditable = order.stop_price !== null && order.triggered_at === null

  const save = async () => {
    const shares = Number(qty)
    if (!Number.isInteger(shares) || shares < 1) {
      setError('Quantity must be a whole number of shares')
      return
    }
    const change: OrderChange = { qty: shares, tif }
    if (order.limit_price !== null) change.limit_price = Number(limit)
    if (stopEditable) change.stop_price = Number(stop)
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
    <Modal labelledBy="modify-title" onClose={onClose}>
      <h2 id="modify-title">
        Modify order #{order.id}: {order.side === 'buy' ? 'Buy' : 'Sell'} {order.symbol}, {orderTypeLabel(order)}
      </h2>
      {order.status !== 'working' ? (
        <>
          <p>This order can't be changed any more: {statusText(order)}.</p>
          <div className="dialog-actions">
            <button className="btn" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : (
        <>
          <label className="field">
            <span>Quantity (shares)</span>
            <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
          </label>
          {stopEditable && (
            <label className="field">
              <span>Stop price</span>
              <input type="number" min={0.01} step={0.01} value={stop} onChange={(e) => setStop(e.target.value)} />
            </label>
          )}
          {order.stop_price !== null && !stopEditable && (
            <p className="muted">The stop already triggered, so this order is working as a limit order.</p>
          )}
          {order.limit_price !== null && (
            <label className="field">
              <span>Limit price</span>
              <input type="number" min={0.01} step={0.01} value={limit} onChange={(e) => setLimit(e.target.value)} />
            </label>
          )}
          <TifSelect value={tif} onChange={setTif} />
          <p className="muted">
            The order is canceled and replaced by a new one with your changes, as at a real broker. If the new order
            can trade right away, it fills.
          </p>
          <div className="dialog-actions">
            <button className="btn primary" disabled={busy} onClick={save}>
              Save changes
            </button>
            <button className="btn" disabled={busy} onClick={onClose}>
              Cancel
            </button>
          </div>
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  )
}
