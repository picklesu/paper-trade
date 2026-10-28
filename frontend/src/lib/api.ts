import type { Account, ClockState, GotoAction, Order, OrderType, Side, TimeInForce } from './types'

export async function updateClock(update: { speed?: number; paused?: boolean }): Promise<ClockState> {
  const resp = await fetch('/api/clock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  })
  if (!resp.ok) throw new Error(`clock update failed: ${resp.status}`)
  return resp.json()
}

export interface GotoResult {
  action: GotoAction
  /** Where the market lands, after snapping to market hours. */
  target: string
  // Rewind and reset previews: what would be erased.
  erased_fills?: Order[]
  erased_orders?: number
  // Rewind previews: the account at the target time.
  cash_now?: number
  cash_then?: number
  positions?: { symbol: string; qty: number; price_now: number; price_then: number }[]
  // Reset previews.
  starting_cash?: number
}

/** `time` is New York wall-clock time, e.g. "2026-03-02T10:30". */
export async function previewGoto(time: string): Promise<GotoResult> {
  const resp = await fetch(`/api/clock/goto?time=${encodeURIComponent(time)}`)
  if (!resp.ok) throw new Error(`goto preview failed: ${resp.status}`)
  return resp.json()
}

export async function goto(time: string, future: 'replay' | 'new' = 'replay'): Promise<GotoResult> {
  const resp = await fetch('/api/clock/goto', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ time, future }),
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `goto failed: ${resp.status}`)
  }
  return resp.json()
}

/** Throws an Error carrying the server's explanation (FastAPI's `detail`). */
async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    const data = await resp.json().catch(() => null)
    throw new Error(typeof data?.detail === 'string' ? data.detail : `request failed: ${resp.status}`)
  }
  return resp.json()
}

export interface OrderRequest {
  symbol: string
  side: Side
  qty: number
  type: OrderType
  tif: TimeInForce
  limit_price?: number
  stop_price?: number
}

export function placeOrder(order: OrderRequest): Promise<Order> {
  return send('/api/orders', 'POST', order)
}

export function cancelOrder(id: number): Promise<Order> {
  return send(`/api/orders/${id}`, 'DELETE')
}

export function resetAccount(startingCash: number): Promise<Account> {
  return send('/api/account/reset', 'POST', { starting_cash: startingCash })
}
