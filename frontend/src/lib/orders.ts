import { orderTypeLabel, price } from './format'
import type { Order } from './types'

/** Follow an order through replacements to the newest version. */
export function latestVersion(order: Order, orders: Order[]): Order {
  let current = order
  for (;;) {
    const next = orders.find((o) => o.replaces === current.id)
    if (!next) return current
    current = next
  }
}

export function describeOrder(o: Order): { text: string; tone: 'ok' | 'info' | 'error' } {
  const what = `${o.qty.toLocaleString()} ${o.symbol}`
  switch (o.status) {
    case 'filled':
      return { text: `${o.side === 'buy' ? 'Bought' : 'Sold'} ${what} at ${price(o.fill_price!)}`, tone: 'ok' }
    case 'working':
      return {
        text: `Order #${o.id} working: ${o.side === 'buy' ? 'buy' : 'sell'} ${what}, ${orderTypeLabel(o)}, ${
          o.tif === 'gtc' ? 'GTC' : 'Day'
        }${o.triggered_at ? ' (stop hit)' : ''}`,
        tone: 'info',
      }
    case 'expired':
      return { text: `Order #${o.id} expired at the close without filling`, tone: 'info' }
    case 'canceled':
      return { text: `Order #${o.id} canceled`, tone: 'info' }
    case 'rejected':
      return { text: `Order #${o.id} rejected: ${o.reason}`, tone: 'error' }
  }
}
