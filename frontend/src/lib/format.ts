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
  })
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
  const [datePart, timePart] = value.split('T')
  const [y, m, d] = datePart.split('-').map(Number)
  const shifted = new Date(Date.UTC(y, m - 1, d + days))
  return `${shifted.toISOString().slice(0, 10)}T${timePart}`
}
