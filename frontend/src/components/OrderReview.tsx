import { dollars, orderTypeLabel, price, signedDollars } from '../lib/format'
import { tifLabel } from '../lib/orders'
import { optionAction } from '../lib/options'
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
  const o = p.option
  const action = o ? optionAction(p.side, p.effect === 'close' ? (buy ? -1 : 1) : 0, o.right) : buy ? 'Buy' : 'Sell'
  const units = o ? 'contracts' : 'shares'
  return (
    <div className="order-review" aria-label="Review order">
      <h3>Review order</h3>
      <p className="review-headline">
        <span className={buy ? 'up' : 'down'}>{action}</span> {p.qty.toLocaleString()} {o ? o.label : p.symbol} ·{' '}
        {orderTypeLabel(p)} · {tifLabel(p.tif)}
      </p>
      <dl className="ticket-summary">
        <Row
          label="Estimated price"
          value={`${price(p.estimated_price)}${o ? ' a share' : ''} ${p.fills_now ? '(fills now)' : '(when it fills)'}`}
        />
        <Row label={buy ? 'Estimated cost' : o ? 'Estimated credit' : 'Estimated proceeds'} value={dollars(p.estimated_total)} />
        <Row label="Commission" value={dollars(p.commission)} />
        <Row label="Buying power" value={`${dollars(p.buying_power_before)} → ${dollars(p.buying_power_after)}`} />
        <Row
          label="Position"
          value={`${p.position_before.toLocaleString()} → ${p.position_after.toLocaleString()} ${units}`}
        />
        {!o && buy && <Row label="Average cost after" value={price(p.avg_cost_after)} />}
        {o && p.breakeven != null && <Row label="Breakeven at expiration" value={price(p.breakeven)} />}
        {o && p.max_loss != null && <Row label="Most you can lose" value={dollars(p.max_loss)} />}
        {o && p.max_gain != null && <Row label="Most you can make" value={dollars(p.max_gain)} />}
        {!!p.collateral && <Row label="Cash set aside" value={dollars(p.collateral)} />}
        {!!p.shares_locked && <Row label="Shares locked" value={`${p.shares_locked.toLocaleString()} ${o!.underlying}`} />}
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
