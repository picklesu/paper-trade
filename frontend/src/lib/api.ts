import type { Bar, Interval } from './bars'
import type {
  Account,
  ClockState,
  ExercisePreview,
  Expiration,
  GotoAction,
  OptionChain,
  Order,
  OrderPreview,
  OrderType,
  QuoteStats,
  Side,
  SymbolInfo,
  TimeInForce,
  Watchlist,
} from './types'

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

/** Start an uncertainty shock at the next open. */
export function triggerShock(): Promise<{ day: string }> {
  return send('/api/shock', 'POST')
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

export function previewExercise(symbol: string, qty: number): Promise<ExercisePreview> {
  return send('/api/options/exercise/preview', 'POST', { symbol, qty })
}

/** Exercise long contracts now: returns the records (the option and the shares). */
export function exerciseOptions(symbol: string, qty: number): Promise<Order[]> {
  return send('/api/options/exercise', 'POST', { symbol, qty })
}

export function fetchOptionChain(symbol: string, expiration: string): Promise<OptionChain> {
  return send(`/api/options/chain?symbol=${encodeURIComponent(symbol)}&expiration=${expiration}`, 'GET')
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

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/** A market-wide circuit-breaker halt: when it began and when trading resumed (Unix seconds; null: not that day). */
export interface HaltEvent {
  start: number
  resume: number | null
  level: number
  /** A real day's halt, placed on the generated path inside the real bar (so the time of day is approximate). */
  estimated?: boolean
}

export async function fetchHalts(start: number, end: number): Promise<HaltEvent[]> {
  const params = new URLSearchParams({ start: new Date(start * 1000).toISOString(), end: new Date(end * 1000).toISOString() })
  const list = await send<unknown>(`/api/halts?${params}`, 'GET')
  return Array.isArray(list)
    ? list.filter((h): h is HaltEvent => isObject(h) && typeof h.start === 'number' && typeof h.level === 'number')
    : []
}

export async function fetchSymbols(): Promise<SymbolInfo[]> {
  const list = await send<unknown>('/api/symbols', 'GET')
  return Array.isArray(list) ? list.filter((s): s is SymbolInfo => isObject(s) && typeof s.symbol === 'string') : []
}

export async function fetchQuoteStats(): Promise<Record<string, QuoteStats>> {
  const stats = await send<unknown>('/api/quote-stats', 'GET')
  return isObject(stats) ? (stats as Record<string, QuoteStats>) : {}
}

export interface Sparklines {
  step_minutes: number
  /** The session's 9:30 open (ISO). */
  session_open: string
  /** Today's prices every `step_minutes` so far, ending with the latest. */
  series: Record<string, number[]>
}

export async function fetchSparklines(symbols: string[]): Promise<Sparklines> {
  const data = await send<Partial<Sparklines>>(`/api/sparklines?symbols=${encodeURIComponent(symbols.join(','))}`, 'GET')
  if (!isObject(data?.series)) throw new Error('unexpected response from the server')
  return data as Sparklines
}

export function createWatchlist(name: string, symbols: string[] = []): Promise<Watchlist> {
  return send('/api/watchlists', 'POST', { name, symbols })
}

/** Rename and/or set the symbols (the whole list in order: add, remove and reorder). */
export function updateWatchlist(id: number, change: { name?: string; symbols?: string[] }): Promise<Watchlist> {
  return send(`/api/watchlists/${id}`, 'PATCH', change)
}

export function deleteWatchlist(id: number): Promise<{ deleted: number }> {
  return send(`/api/watchlists/${id}`, 'DELETE')
}

export async function fetchExpirations(symbol: string): Promise<Expiration[]> {
  const list = await send<unknown>(`/api/options/expirations?symbol=${encodeURIComponent(symbol)}`, 'GET')
  return Array.isArray(list) ? list.filter((e): e is Expiration => isObject(e) && typeof e.date === 'string') : []
}
