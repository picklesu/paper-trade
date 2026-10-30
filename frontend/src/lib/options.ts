import type { ChainCalibration, ChainSide, Expiration } from './types'

export type ChainColumnId = keyof ChainSide

export interface ChainColumn {
  id: ChainColumnId
  label: string
  title: string
  format: (v: number) => string
}

const fixed = (digits: number) => (v: number) => v.toFixed(digits)
const count = (v: number) => v.toLocaleString('en-US')

/** In the order they sit outward from the strike, on both sides. */
export const CHAIN_COLUMNS: ChainColumn[] = [
  { id: 'bid', label: 'Bid', title: 'Bid', format: fixed(2) },
  { id: 'ask', label: 'Ask', title: 'Ask', format: fixed(2) },
  { id: 'bid_size', label: 'Bid Sz', title: 'Bid size (contracts)', format: count },
  { id: 'ask_size', label: 'Ask Sz', title: 'Ask size (contracts)', format: count },
  { id: 'mark', label: 'Mark', title: 'Mark (between bid and ask)', format: fixed(2) },
  { id: 'iv', label: 'IV', title: 'Implied volatility', format: (v) => `${(v * 100).toFixed(1)}%` },
  { id: 'delta', label: 'Delta', title: 'Delta', format: fixed(2) },
  { id: 'gamma', label: 'Gamma', title: 'Gamma', format: fixed(4) },
  { id: 'theta', label: 'Theta', title: 'Theta (per day)', format: fixed(3) },
  { id: 'vega', label: 'Vega', title: 'Vega (per volatility point)', format: fixed(3) },
  { id: 'rho', label: 'Rho', title: 'Rho (per rate point)', format: fixed(3) },
  { id: 'volume', label: 'Volume', title: 'Volume today', format: count },
  { id: 'oi', label: 'Open Int', title: 'Open interest', format: count },
]

export const CHAIN_COLUMN_BY_ID = Object.fromEntries(CHAIN_COLUMNS.map((c) => [c.id, c])) as Record<ChainColumnId, ChainColumn>
export const DEFAULT_CHAIN_COLUMNS: ChainColumnId[] = ['bid', 'ask', 'iv', 'delta', 'oi']

export function sanitizeChainColumns(raw: unknown): ChainColumnId[] | null {
  if (!Array.isArray(raw)) return null
  const ids = raw.filter((c): c is ChainColumnId => typeof c === 'string' && c in CHAIN_COLUMN_BY_ID)
  return ids.length ? [...new Set(ids)] : null // none: the defaults
}

/** Turn a column on or off; the last one stays (a chain of bare strikes shows nothing). */
export function toggleChainColumn(columns: ChainColumnId[], id: ChainColumnId): ChainColumnId[] {
  const on = new Set(columns)
  if (on.has(id)) {
    if (on.size === 1) return columns
    on.delete(id)
  } else on.add(id)
  return CHAIN_COLUMNS.map((c) => c.id).filter((c) => on.has(c))
}

/** How many strikes to show, centered on the price (0: all). */
export const STRIKE_COUNTS = [8, 12, 20, 40, 0]

/** The range of strike indexes to show: `count` around the price (all for 0). */
export function strikeWindow(strikes: number[], spot: number, count: number): [number, number] {
  if (!count || count >= strikes.length) return [0, strikes.length]
  const above = strikes.findIndex((k) => k >= spot)
  const center = above < 0 ? strikes.length : above
  const start = Math.min(Math.max(0, center - Math.floor(count / 2)), strikes.length - count)
  return [start, start + count]
}

/** Where the price falls among the shown strikes: the index of the first strike at or
 *  above it (the price line goes just before that row). */
export const priceRow = (strikes: number[], spot: number) => {
  const i = strikes.findIndex((k) => k >= spot)
  return i < 0 ? strikes.length : i
}

const KIND_LETTER: Record<Expiration['kind'], string> = { daily: 'D', weekly: 'W', quarterly: 'Q', monthly: 'M', leaps: 'M' }

/** "Nov 20 '26 (21d) M": the date, days left and kind (daily, weekly, quarterly: month- or quarter-end, monthly). */
export function expirationLabel(e: Expiration): string {
  const [y, m, d] = e.date.split('-').map(Number)
  const day = new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  return `${day} '${String(y).slice(2)} (${e.days === 0 ? 'today' : `${e.days}d`}) ${KIND_LETTER[e.kind]}`
}

const percent = (v: number) => `${(v * 100).toFixed(1)}%`

/** The Options header's note on where the smile's shape comes from: its label and the details. */
export function calibrationNote(c: ChainCalibration, symbol: string): { label: string; title: string } {
  const premium = `Options are priced ${c.premium.toFixed(2)}× the simulated volatility (implied over realized, the volatility risk premium).`
  if (!c.as_of) {
    return {
      label: 'Estimated',
      title: `No real option data for ${symbol} yet: the smile's skew and curvature are built-in estimates. ${premium} With Alpaca keys, the server fits each symbol to its real options once a day.`,
    }
  }
  const lines = [
    `Skew and smile fitted to ${symbol}'s real options on ${shortDate(c.as_of, true)}${c.fit_error != null ? ` (within ${c.fit_error.toFixed(1)} volatility points)` : ''}; the simulation sets the level.`,
    premium,
  ]
  const e = c.earnings
  if (e?.move != null) {
    const when = e.timing === 'pre-market' ? ', before the open' : e.timing === 'post-market' ? ', after the close' : ''
    lines.push(`Real options imply a ${percent(e.move)} move on ${symbol}'s report (${shortDate(e.day, false)}${when}); the simulation has its own earnings dates.`)
  }
  return { label: `Calibrated ${shortDate(c.as_of, false)}`, title: lines.join(' ') }
}

function shortDate(iso: string, year: boolean): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(year ? { year: 'numeric' } : {}) })
}

/** The expiration to show when the list changes: the one picked if it's still open,
 *  otherwise the first on or after it, otherwise the first a week or more out. */
export function pickExpiration(list: Expiration[], wanted: string | null): string | null {
  if (!list.length) return null
  if (wanted) {
    const same = list.find((e) => e.date >= wanted)
    if (same) return same.date
  }
  return (list.find((e) => e.days >= 7) ?? list[0]).date
}
