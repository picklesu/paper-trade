const MARKET_TZ = 'America/New_York'

export function price(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** Treats values that display as 0.00 as zero, so we never show "-0.00". */
function cents(n: number): number {
  return Math.abs(n) < 0.005 ? 0 : n
}

export function signed(n: number): string {
  const v = cents(n)
  return (v > 0 ? '+' : '') + price(v)
}

export function percent(n: number): string {
  return `${signed(n)}%`
}

export function volume(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return String(n)
}

/** CSS class for coloring a change: up, down or flat. */
export function direction(n: number): 'up' | 'down' | 'flat' {
  const v = cents(n)
  return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'
}

export function marketDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    timeZone: MARKET_TZ,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

/** A calendar date like "2026-10-28" as "Oct 28" (no time zone involved). */
export function calendarDate(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function marketTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { timeZone: MARKET_TZ, hour12: false })
}

export function marketDateTime(iso: string): string {
  const d = new Date(iso)
  const day = d.toLocaleDateString('en-US', {
    timeZone: MARKET_TZ,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
  const time = d.toLocaleTimeString('en-US', { timeZone: MARKET_TZ, hour: 'numeric', minute: '2-digit' })
  return `${day}, ${time} ET`
}

/** "YYYY-MM-DDTHH:mm" in New York time, the format of <input type="datetime-local">. */
export function toMarketInput(iso: string): string {
  // The sv-SE locale formats as "YYYY-MM-DD HH:mm:ss".
  return new Date(iso).toLocaleString('sv-SE', { timeZone: MARKET_TZ }).replace(' ', 'T').slice(0, 16)
}

/** Shift a "YYYY-MM-DDTHH:mm" wall-clock value by whole days, keeping the time of day. */
export function shiftInputDays(value: string, days: number): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return value
  const [datePart, timePart] = value.split('T')
  const [y, m, d] = datePart.split('-').map(Number)
  const shifted = new Date(Date.UTC(y, m - 1, d + days))
  return `${shifted.toISOString().slice(0, 10)}T${timePart}`
}

export function dollars(n: number): string {
  return (n < 0 ? '-$' : '$') + price(Math.abs(n))
}

export function signedDollars(n: number): string {
  const v = cents(n)
  return (v > 0 ? '+' : '') + dollars(v)
}

/** Compact market time for tables: "Jan 5 10:30:15". */
export function marketStamp(iso: string): string {
  const d = new Date(iso)
  const day = d.toLocaleDateString('en-US', { timeZone: MARKET_TZ, month: 'short', day: 'numeric' })
  return `${day} ${marketTime(iso)}`
}

const TYPE_LABEL = {
  market: 'Market',
  limit: 'Limit',
  stop: 'Stop',
  stop_limit: 'Stop limit',
  exercise: 'Exercise',
  assignment: 'Assignment',
  expiration: 'Expiration',
  dividend: 'Dividend',
} as const

/** "Limit 580.00", "Stop 570.00", "Stop limit 570.00 / 571.00", "Market". */
export function orderTypeLabel(o: { type: keyof typeof TYPE_LABEL; limit_price: number | null; stop_price: number | null }): string {
  const parts: string[] = [TYPE_LABEL[o.type]]
  if (o.stop_price !== null) parts.push(price(o.stop_price))
  if (o.limit_price !== null) parts.push((o.stop_price !== null ? '/ ' : '') + price(o.limit_price))
  return parts.join(' ')
}
