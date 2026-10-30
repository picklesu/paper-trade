import type { Quote, QuoteStats } from './types'

/** What a watchlist row knows about its symbol. */
export interface Row {
  symbol: string
  quote?: Quote
  stats?: QuoteStats
  /** Shares held (0: none). */
  qty: number
}

export type ColumnId =
  | 'last'
  | 'change'
  | 'change_pct'
  | 'bid'
  | 'ask'
  | 'bid_size'
  | 'ask_size'
  | 'spread'
  | 'volume'
  | 'avg_volume'
  | 'range'
  | 'high_52w'
  | 'low_52w'
  | 'spark'
  | 'position'

export interface Column {
  id: ColumnId
  /** Header text (short: the panel is narrow). */
  label: string
  /** The name in the Columns menu and the header's tooltip. */
  title: string
  value: (row: Row) => number | null
}

/** Today folds into the 52-week range, which comes from finished sessions. */
export const high52 = (r: Row) => (r.stats && r.quote ? Math.max(r.stats.high_52w, r.quote.high) : null)
export const low52 = (r: Row) => (r.stats && r.quote ? Math.min(r.stats.low_52w, r.quote.low) : null)
/** Where the last price sits in the day's range: 0 at the low, 1 at the high. */
export const rangePosition = (q: Quote) => (q.high > q.low ? (q.last - q.low) / (q.high - q.low) : 0.5)

const fromQuote = (f: (q: Quote) => number) => (r: Row) => (r.quote ? f(r.quote) : null)

export const COLUMNS: Column[] = [
  { id: 'last', label: 'Last', title: 'Last price', value: fromQuote((q) => q.last) },
  { id: 'change', label: 'Chg', title: 'Change', value: fromQuote((q) => q.change) },
  { id: 'change_pct', label: 'Chg %', title: 'Change %', value: fromQuote((q) => q.change_pct) },
  { id: 'spark', label: 'Today', title: 'Today’s chart', value: fromQuote((q) => q.change_pct) },
  { id: 'range', label: 'Day Range', title: 'Day range (low to high)', value: fromQuote(rangePosition) },
  { id: 'bid', label: 'Bid', title: 'Bid', value: fromQuote((q) => q.bid) },
  { id: 'ask', label: 'Ask', title: 'Ask', value: fromQuote((q) => q.ask) },
  { id: 'bid_size', label: 'Bid Sz', title: 'Bid size', value: fromQuote((q) => q.bid_size) },
  { id: 'ask_size', label: 'Ask Sz', title: 'Ask size', value: fromQuote((q) => q.ask_size) },
  { id: 'spread', label: 'Spread', title: 'Spread (ask − bid)', value: fromQuote((q) => Math.round((q.ask - q.bid) * 100) / 100) },
  { id: 'volume', label: 'Volume', title: 'Volume today', value: fromQuote((q) => q.volume) },
  { id: 'avg_volume', label: 'Avg Vol', title: 'Average daily volume (30 days)', value: (r) => r.stats?.avg_volume ?? null },
  { id: 'high_52w', label: '52W High', title: '52-week high', value: high52 },
  { id: 'low_52w', label: '52W Low', title: '52-week low', value: low52 },
  { id: 'position', label: 'Pos', title: 'Shares held', value: (r) => r.qty },
]

export const COLUMN_BY_ID = Object.fromEntries(COLUMNS.map((c) => [c.id, c])) as Record<ColumnId, Column>
export const DEFAULT_COLUMNS: ColumnId[] = ['last', 'change_pct', 'spark']

export function sanitizeColumns(raw: unknown): ColumnId[] | null {
  if (!Array.isArray(raw)) return null
  const ids = raw.filter((c): c is ColumnId => typeof c === 'string' && c in COLUMN_BY_ID)
  // Nothing usable (e.g. saved by a build with other column names): the defaults.
  return ids.length || raw.length === 0 ? [...new Set(ids)] : null
}

/** Columns turned on, in the menu's order. */
export function toggleColumn(columns: ColumnId[], id: ColumnId): ColumnId[] {
  const on = new Set(columns)
  if (on.has(id)) on.delete(id)
  else on.add(id)
  return COLUMNS.map((c) => c.id).filter((c) => on.has(c))
}

export type SortKey = ColumnId | 'symbol'
export interface Sort {
  key: SortKey
  dir: 1 | -1
}

export function sanitizeSort(raw: unknown): Sort | null {
  if (typeof raw !== 'object' || raw === null) return null
  const { key, dir } = raw as { key?: unknown; dir?: unknown }
  const keyOk = key === 'symbol' || (typeof key === 'string' && key in COLUMN_BY_ID)
  return keyOk && (dir === 1 || dir === -1) ? { key: key as SortKey, dir } : null
}

/** A header click: symbols sort A–Z first and numbers high to low; the second
 *  click reverses; the third goes back to the list's own order (null). */
export function nextSort(sort: Sort | null, key: SortKey): Sort | null {
  const first: 1 | -1 = key === 'symbol' ? 1 : -1
  if (!sort || sort.key !== key) return { key, dir: first }
  return sort.dir === first ? { key, dir: -first as 1 | -1 } : null
}

/** Rows in sort order (the list's own order when `sort` is null). Rows without a
 *  value go last either way. */
export function sortRows(rows: Row[], sort: Sort | null): Row[] {
  if (!sort) return rows
  if (sort.key === 'symbol') return [...rows].sort((a, b) => sort.dir * a.symbol.localeCompare(b.symbol))
  const value = COLUMN_BY_ID[sort.key].value
  return [...rows].sort((a, b) => {
    const va = value(a)
    const vb = value(b)
    if (va === null || vb === null) return va === null ? (vb === null ? 0 : 1) : -1
    return sort.dir * (va - vb)
  })
}

/** `symbols` with `symbol` moved to just before `before` (null: to the end). */
export function moveSymbol(symbols: string[], symbol: string, before: string | null): string[] {
  const rest = symbols.filter((s) => s !== symbol)
  const at = before === null ? rest.length : rest.indexOf(before)
  if (at < 0 || rest.length === symbols.length) return symbols
  const moved = [...rest.slice(0, at), symbol, ...rest.slice(at)]
  // Dropped where it already was: the same array, so callers can tell nothing changed.
  return moved.every((s, i) => s === symbols[i]) ? symbols : moved
}
