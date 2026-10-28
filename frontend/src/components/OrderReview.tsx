import { dollars, orderTypeLabel, price, signedDollars } from '../lib/format'
import { tifLabel } from '../lib/orders'
import type { OrderPreview } from '../lib/types'

interface Props {
  preview: OrderPreview
  busy: boolean
  onPlace: () => void
  onEdit: () => void
}

/** The confirm step: what the order will do, from the server's preview. */
export function OrderReview({ preview: p, busy, onPlace, onEdit }: Props) {
  const buy = p.side === 'buy'
  return (
    <div className="order-review" aria-label="Review order">
      <h3>Review order</h3>
      <p className="review-headline">
        <span className={buy ? 'up' : 'down'}>{buy ? 'Buy' : 'Sell'}</span> {p.qty.toLocaleString()} {p.symbol} ·{' '}
        {orderTypeLabel(p)} · {tifLabel(p.tif)}
      </p>
      <dl className="ticket-summary">
        <Row label="Estimated price" value={`${price(p.estimated_price)} ${p.fills_now ? '(fills now)' : '(when it fills)'}`} />
        <Row label={buy ? 'Estimated cost' : 'Estimated proceeds'} value={dollars(p.estimated_total)} />
        <Row label="Commission" value={dollars(p.commission)} />
        <Row label="Buying power" value={`${dollars(p.buying_power_before)} → ${dollars(p.buying_power_after)}`} />
        <Row
          label="Position"
          value={`${p.position_before.toLocaleString()} → ${p.position_after.toLocaleString()} shares`}
        />
        {buy && <Row label="Average cost after" value={price(p.avg_cost_after)} />}
        {p.estimated_realized !== null && (
          <Row label="Estimated realized P&L" value={signedDollars(p.estimated_realized)} />
        )}
      </dl>
      {p.warnings.length > 0 && (
        <ul className="review-warnings">
          {p.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <p className="muted small">Estimates use current prices and can change by the time the order fills.</p>
      <div className="dialog-actions">
        <button type="button" className={`btn submit ${p.side}`} disabled={busy} onClick={onPlace}>
          Place order
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onEdit}>
          Edit
        </button>
      </div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
