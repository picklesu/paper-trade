import { orderTypeLabel, price } from './format'
import type { Order, OrderStatus, TimeInForce } from './types'

export const STATUS_LABEL: Record<OrderStatus, string> = {
  working: 'Working',
  filled: 'Filled',
  canceled: 'Canceled',
  expired: 'Expired',
  rejected: 'Rejected',
}

export const TIF_OPTIONS: { value: TimeInForce; label: string }[] = [
  { value: 'day', label: 'Day (expires at 4:00 PM)' },
  { value: 'gtc', label: 'Good till canceled' },
]

export function tifLabel(tif: TimeInForce, short = false): string {
  if (short) return tif === 'gtc' ? 'GTC' : 'Day'
  return tif === 'gtc' ? 'Good till canceled' : 'Day'
}

/** One line of status, shared by the Orders tab and the ticket: "Filled at 230.01", "Working (stop hit)". */
export function statusText(o: Order): string {
  let text = STATUS_LABEL[o.status]
  if (o.status === 'filled') text += ` at ${price(o.fill_price!)}`
  if (o.status === 'working' && o.triggered_at) text += ' (stop hit)'
  if (o.reason) text += ` · ${o.reason}`
  return text
}

/** Map from an order id to the order that replaced it. */
export function replacements(orders: Order[]): Map<number, Order> {
  const map = new Map<number, Order>()
  for (const o of orders) if (o.replaces !== null) map.set(o.replaces, o)
  return map
}

/** Follow an order through replacements to the newest version. */
export function latestVersion(order: Order, replacedBy: Map<number, Order>): Order {
  let current = order
  for (let next = replacedBy.get(current.id); next; next = replacedBy.get(current.id)) current = next
  return current
}

/** The ticket's sentence about an order, with a tone for coloring. */
export function describeOrder(o: Order): { text: string; tone: 'ok' | 'info' | 'error' } {
  const what = `${o.qty.toLocaleString()} ${o.symbol}`
  if (o.status === 'filled') {
    return { text: `${o.side === 'buy' ? 'Bought' : 'Sold'} ${what} at ${price(o.fill_price!)}`, tone: 'ok' }
  }
  if (o.status === 'working') {
    return {
      text: `Order #${o.id} working: ${o.side} ${what}, ${orderTypeLabel(o)}, ${tifLabel(o.tif, true)}${
        o.triggered_at ? ' (stop hit)' : ''
      }`,
      tone: 'info',
    }
  }
  return { text: `Order #${o.id}: ${statusText(o)}`, tone: o.status === 'rejected' ? 'error' : 'info' }
}
