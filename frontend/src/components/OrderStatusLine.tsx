import { describeOrder, latestVersion } from '../lib/orders'
import type { Order } from '../lib/types'

/** The ticket's live line about the last order placed from it. */
export function OrderStatusLine({ order, orders }: { order: Order; orders: Order[] }) {
  // The stream is the source of truth once it has the order; until then, the placement response.
  const live = latestVersion(orders.find((o) => o.id === order.id) ?? order, orders)
  const { text, tone } = describeOrder(live)
  return (
    <p className={`ticket-result ${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      {text}
    </p>
  )
}
