import { useMemo } from 'react'
import { describeOrder, latestVersion, replacements } from '../lib/orders'
import type { Order } from '../lib/types'

/** The ticket's live line about the last order placed from it. */
export function OrderStatusLine({ order, orders }: { order: Order; orders: Order[] }) {
  const byId = useMemo(() => new Map(orders.map((o) => [o.id, o])), [orders])
  const replacedBy = useMemo(() => replacements(orders), [orders])
  // The stream is the source of truth once it has the order; until then, the placement response.
  const live = latestVersion(byId.get(order.id) ?? order, replacedBy)
  const { text, tone } = describeOrder(live)
  return (
    <p className={`ticket-result ${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      {text}
    </p>
  )
}
