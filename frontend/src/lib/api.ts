import type { Bar, Interval } from './bars'
import type { Account, ClockState, GotoAction, Order, OrderPreview, OrderType, Side, TimeInForce } from './types'

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

export interface RealPricesStatus {
  /** Whether an Alpha Vantage key is set up (backend/.env). */
  configured: boolean
  /** Symbols with a saved real price, of `total`. */
  available: number
  total: number
  /** Trading days of the oldest and newest saved prices ("YYYY-MM-DD"). */
  oldest?: string | null
  newest?: string | null
  requests_left?: number
  daily_requests?: number
}

export async function fetchRealPrices(): Promise<RealPricesStatus> {
  const resp = await fetch('/api/real-prices')
  if (!resp.ok) throw new Error(`real prices failed: ${resp.status}`)
  return resp.json()
}

/** Continue the market from now at the saved real prices. */
export async function syncRealPrices(): Promise<RealPricesStatus> {
  const resp = await fetch('/api/real-prices/sync', { method: 'POST' })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `sync failed: ${resp.status}`)
  }
  return resp.json()
}

/** FastAPI's `detail`: a message, or a list of validation errors like "qty: must be an integer". */
function errorText(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => `${Array.isArray(d?.loc) ? d.loc.filter((l: unknown) => l !== 'body').join('.') + ': ' : ''}${d?.msg ?? ''}`)
      .join('; ')
  }
  return null
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
    throw new Error(errorText(data?.detail) ?? `request failed: ${resp.status}`)
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

export function previewOrder(order: OrderRequest): Promise<OrderPreview> {
  return send('/api/orders/preview', 'POST', order)
}

export interface OrderChange {
  qty?: number
  limit_price?: number
  stop_price?: number
  tif?: TimeInForce
}

/** Cancel/replace a working order; resolves to the new order. */
export function replaceOrder(id: number, change: OrderChange): Promise<Order> {
  return send(`/api/orders/${id}`, 'PATCH', change)
}

export function cancelOrder(id: number): Promise<Order> {
  return send(`/api/orders/${id}`, 'DELETE')
}

export function resetAccount(startingCash: number): Promise<Account> {
  return send('/api/account/reset', 'POST', { starting_cash: startingCash })
}

export interface BarsPage {
  bars: Bar[]
  /** Whether older bars exist before the first one. */
  more: boolean
}

/** Chart bars, oldest first: the latest `count`, or those before `end` (Unix seconds). */
export async function fetchBars(symbol: string, interval: Interval, end?: number, count = 500): Promise<BarsPage> {
  const params = new URLSearchParams({ symbol, interval, count: String(count) })
  if (end !== undefined) params.set('end', new Date(end * 1000).toISOString())
  const page = await send<Partial<BarsPage>>(`/api/bars?${params}`, 'GET')
  // Never hand the chart something it can't draw.
  if (!Array.isArray(page?.bars)) throw new Error('unexpected response from the server')
  return { bars: page.bars, more: Boolean(page.more) }
}
