import type { Order } from './types'

/** [time (Unix seconds, bar start), open, high, low, close, volume] */
export type Bar = [number, number, number, number, number, number]

export type Interval = '1m' | '5m' | '15m' | '30m' | '1h' | '1D' | '1W'

export const INTERVALS: Interval[] = ['1m', '5m', '15m', '30m', '1h', '1D', '1W']

/** Button labels for bar sizes ("D"/"W" so they don't read like the 1D range) and their descriptions. */
export const INTERVAL_LABEL: Record<Interval, { short: string; long: string }> = {
  '1m': { short: '1m', long: '1-minute bars' },
  '5m': { short: '5m', long: '5-minute bars' },
  '15m': { short: '15m', long: '15-minute bars' },
  '30m': { short: '30m', long: '30-minute bars' },
  '1h': { short: '1h', long: '1-hour bars' },
  '1D': { short: 'D', long: 'Daily bars' },
  '1W': { short: 'W', long: 'Weekly bars' },
}

/** Minutes per bar for intraday intervals. */
export const INTRADAY_MINUTES: Partial<Record<Interval, number>> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60 }

const DAY = 86_400

export interface RangePreset {
  label: string
  interval: Interval
  /** Seconds of history to show, or 'session' (today) / 'ytd'. */
  span: number | 'session' | 'ytd'
}

/** Robinhood/Fidelity-style ranges, each with a sensible bar size. */
export const RANGES: RangePreset[] = [
  { label: '1D', interval: '1m', span: 'session' },
  { label: '5D', interval: '5m', span: 7 * DAY },
  { label: '1M', interval: '30m', span: 31 * DAY },
  { label: '3M', interval: '1h', span: 92 * DAY },
  { label: '6M', interval: '1D', span: 183 * DAY },
  { label: 'YTD', interval: '1D', span: 'ytd' },
  { label: '1Y', interval: '1D', span: 365 * DAY },
  { label: '5Y', interval: '1W', span: 5 * 365 * DAY },
]

const ET = 'America/New_York'

/** Start of the visible range for a preset, in Unix seconds. */
export function rangeStart(preset: RangePreset, now: number, sessionOpen: number): number {
  if (preset.span === 'session') return sessionOpen
  if (preset.span === 'ytd') {
    const year = new Date(now * 1000).toLocaleString('en-US', { timeZone: ET, year: 'numeric' })
    return Date.UTC(Number(year), 0, 1) / 1000
  }
  return now - preset.span
}

/** Monday's date (YYYY-MM-DD, New York) of the week a time falls in. */
export function weekKey(t: number): string {
  const day = new Date(t * 1000).toLocaleDateString('en-CA', { timeZone: ET }) // YYYY-MM-DD
  const d = new Date(`${day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7))
  return d.toISOString().slice(0, 10)
}

/**
 * Start time of the bar that market time `now` belongs to. Intraday bars are
 * aligned to the session's 9:30 open; a daily bar starts at the open; a weekly
 * bar keeps the start of the last bar if it's the same week.
 */
export function bucketStart(interval: Interval, now: number, sessionOpen: number, lastBarTime: number | null): number {
  const minutes = INTRADAY_MINUTES[interval]
  if (minutes) return sessionOpen + Math.floor((now - sessionOpen) / (minutes * 60)) * minutes * 60
  if (interval === '1W' && lastBarTime !== null && weekKey(lastBarTime) === weekKey(sessionOpen)) return lastBarTime
  return sessionOpen
}

/** Fold the latest trade price into the bar list: update the last bar or start a new one. */
export function applyTick(bars: Bar[], bucket: number, price: number): Bar[] {
  const last = bars[bars.length - 1]
  if (last && last[0] === bucket) {
    const updated: Bar = [bucket, last[1], Math.max(last[2], price), Math.min(last[3], price), price, last[5]]
    return [...bars.slice(0, -1), updated]
  }
  if (last && bucket < last[0]) return bars // stale tick; a reload is coming
  return [...bars, [bucket, price, price, price, price, 0]]
}

/** Combine pages of bars (either order), keeping one bar per time; newer copies win. */
export function mergeBars(a: Bar[], b: Bar[]): Bar[] {
  const byTime = new Map<number, Bar>()
  for (const bar of a) byTime.set(bar[0], bar)
  for (const bar of b) byTime.set(bar[0], bar)
  return [...byTime.values()].sort((x, y) => x[0] - y[0])
}

export interface FillMarker {
  time: number
  side: 'buy' | 'sell'
  text: string
}

/** How long a bar of this size covers, in seconds (a daily bar spans its day, a weekly one its week). */
export function barSeconds(interval: Interval): number {
  const minutes = INTRADAY_MINUTES[interval]
  return minutes ? minutes * 60 : interval === '1W' ? 7 * 86400 : 86400
}

export interface HaltMarker {
  time: number
  text: string
}

/** One marker per circuit-breaker halt, on the bar it began in (halts before the first bar are left out). */
export function haltMarkers(bars: Bar[], halts: { start: number; resume: number | null; level: number; estimated?: boolean }[], interval: Interval): HaltMarker[] {
  if (!bars.length) return []
  const end = bars[bars.length - 1][0] + barSeconds(interval) // the last bar's span: a halt after it isn't on the chart
  const out: HaltMarker[] = []
  for (const h of halts) {
    if (h.start < bars[0][0] || h.start >= end) continue
    let lo = 0
    let hi = bars.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars[mid][0] <= h.start) lo = mid
      else hi = mid - 1
    }
    const text = h.resume === null ? `Halt L${h.level} (closed)` : `Halt L${h.level}`
    out.push({ time: bars[lo][0], text: h.estimated ? `${text} est.` : text })
  }
  return out.sort((a, b) => a.time - b.time)
}

/** One marker per fill of `symbol`, placed on the bar the fill happened in. */
export function fillMarkers(bars: Bar[], orders: Order[], symbol: string): FillMarker[] {
  if (!bars.length) return []
  const out: FillMarker[] = []
  for (const o of orders) {
    if (o.symbol !== symbol || o.status !== 'filled' || !o.filled_at || o.type === 'dividend') continue // a dividend isn't a trade
    const t = Date.parse(o.filled_at) / 1000
    if (t < bars[0][0]) continue
    // Last bar starting at or before the fill (binary search).
    let lo = 0
    let hi = bars.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars[mid][0] <= t) lo = mid
      else hi = mid - 1
    }
    out.push({ time: bars[lo][0], side: o.side, text: `${o.side === 'buy' ? 'B' : 'S'} ${o.qty}` })
  }
  return out.sort((a, b) => a.time - b.time)
}

/** Crosshair label for a bar's time, in New York time. */
export function formatBarTime(t: number, interval: Interval): string {
  const d = new Date(t * 1000)
  if (interval === '1W') {
    return `Week of ${d.toLocaleDateString('en-US', { timeZone: ET, month: 'short', day: 'numeric', year: 'numeric' })}`
  }
  if (interval === '1D') {
    return d.toLocaleDateString('en-US', { timeZone: ET, weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
  }
  const day = d.toLocaleDateString('en-US', { timeZone: ET, weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
  const time = d.toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit' })
  return `${day} ${time}`
}

/** Axis labels by tick type (0 year, 1 month, 2 day, 3 time, 4 time with seconds), New York time. */
export function formatTick(t: number, tickType: number): string {
  const d = new Date(t * 1000)
  switch (tickType) {
    case 0:
      return d.toLocaleDateString('en-US', { timeZone: ET, year: 'numeric' })
    case 1:
      return d.toLocaleDateString('en-US', { timeZone: ET, month: 'short' })
    case 2:
      return d.toLocaleDateString('en-US', { timeZone: ET, month: 'short', day: 'numeric' })
    default:
      return d.toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit' })
  }
}
