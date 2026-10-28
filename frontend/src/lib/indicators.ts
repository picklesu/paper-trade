import type { Bar } from './bars'

/** One value per bar; null where there isn't enough history yet. */
export type Values = (number | null)[]

const close = (bars: Bar[]) => bars.map((b) => b[4])

/** Simple moving average of the last `n` values. */
export function sma(values: number[], n: number): Values {
  const out: Values = new Array(values.length).fill(null)
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= n) sum -= values[i - n]
    if (i >= n - 1) out[i] = sum / n
  }
  return out
}

/** Exponential moving average (smoothing 2/(n+1)), seeded with the SMA of the first `n` values.
 *  Leading nulls in the input are skipped, so it can smooth another indicator. */
export function ema(values: Values, n: number): Values {
  const out: Values = new Array(values.length).fill(null)
  const k = 2 / (n + 1)
  let prev: number | null = null
  let seen = 0
  let seed = 0
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v === null) continue
    if (prev === null) {
      seed += v
      seen += 1
      if (seen === n) prev = out[i] = seed / n
      continue
    }
    prev = out[i] = v * k + prev * (1 - k)
  }
  return out
}

/** Bollinger Bands: SMA ± `mult` population standard deviations over `n` closes. */
export function bollinger(bars: Bar[], n = 20, mult = 2): { middle: Values; upper: Values; lower: Values } {
  const c = close(bars)
  const middle = sma(c, n)
  const upper: Values = new Array(c.length).fill(null)
  const lower: Values = new Array(c.length).fill(null)
  for (let i = n - 1; i < c.length; i++) {
    const mean = middle[i]!
    let variance = 0
    for (let j = i - n + 1; j <= i; j++) variance += (c[j] - mean) ** 2
    const sd = Math.sqrt(variance / n)
    upper[i] = mean + mult * sd
    lower[i] = mean - mult * sd
  }
  return { middle, upper, lower }
}

/** Relative Strength Index with Wilder's smoothing. */
export function rsi(bars: Bar[], n = 14): Values {
  const c = close(bars)
  const out: Values = new Array(c.length).fill(null)
  let gain = 0
  let loss = 0
  for (let i = 1; i < c.length; i++) {
    const change = c[i] - c[i - 1]
    const g = Math.max(change, 0)
    const l = Math.max(-change, 0)
    if (i <= n) {
      gain += g / n
      loss += l / n
      if (i < n) continue
    } else {
      gain = (gain * (n - 1) + g) / n
      loss = (loss * (n - 1) + l) / n
    }
    // No movement at all is neutral (50), not "overbought".
    out[i] = loss === 0 ? (gain === 0 ? 50 : 100) : 100 - 100 / (1 + gain / loss)
  }
  return out
}

/** MACD: EMA(fast) − EMA(slow), its EMA(signal), and the difference (histogram). */
export function macd(bars: Bar[], fast = 12, slow = 26, signal = 9): { macd: Values; signal: Values; histogram: Values } {
  const c = close(bars)
  const f = ema(c, fast)
  const s = ema(c, slow)
  const line: Values = c.map((_, i) => (f[i] !== null && s[i] !== null ? f[i]! - s[i]! : null))
  const sig = ema(line, signal)
  const histogram: Values = line.map((v, i) => (v !== null && sig[i] !== null ? v - sig[i]! : null))
  return { macd: line, signal: sig, histogram }
}

// One formatter, reused: creating one per call (toLocaleDateString with a time zone) is slow.
const NY_DATE = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' })

/** Session key (New York date) of a bar time, so VWAP restarts each day. */
function sessionKey(t: number): string {
  return NY_DATE.format(t * 1000)
}

/** Volume-weighted average price of the typical price (H+L+C)/3, restarting each session. */
export function vwap(bars: Bar[]): Values {
  const out: Values = []
  let key = ''
  let pv = 0
  let vol = 0
  for (const [t, , h, l, c, v] of bars) {
    const k = sessionKey(t)
    if (k !== key) {
      key = k
      pv = 0
      vol = 0
    }
    const typical = (h + l + c) / 3
    pv += typical * v
    vol += v
    out.push(vol > 0 ? pv / vol : typical)
  }
  return out
}

// ----- the standard set shown in the chart's Indicators menu ---------------------------

export type IndicatorId = 'sma20' | 'sma50' | 'sma200' | 'ema9' | 'ema21' | 'vwap' | 'bb' | 'rsi' | 'macd'

export interface IndicatorSetting {
  on: boolean
  /** Lengths, in the order the indicator's label lists them (e.g. MACD fast, slow, signal). */
  params: number[]
}

export type IndicatorSettings = Record<IndicatorId, IndicatorSetting>

export interface IndicatorInfo {
  id: IndicatorId
  name: string
  /** Names of the editable lengths. */
  paramNames: string[]
  /** The lines it draws, in the order `compute` returns them. */
  lines: string[]
  /** Drawn on the price chart, or in its own pane below. */
  pane: 'price' | 'own'
  /** Only meaningful for intraday bars. */
  intradayOnly?: boolean
}

export const INDICATORS: IndicatorInfo[] = [
  { id: 'sma20', name: 'SMA', paramNames: ['Length'], lines: ['value'], pane: 'price' },
  { id: 'sma50', name: 'SMA', paramNames: ['Length'], lines: ['value'], pane: 'price' },
  { id: 'sma200', name: 'SMA', paramNames: ['Length'], lines: ['value'], pane: 'price' },
  { id: 'ema9', name: 'EMA', paramNames: ['Length'], lines: ['value'], pane: 'price' },
  { id: 'ema21', name: 'EMA', paramNames: ['Length'], lines: ['value'], pane: 'price' },
  { id: 'vwap', name: 'VWAP', paramNames: [], lines: ['value'], pane: 'price', intradayOnly: true },
  { id: 'bb', name: 'Bollinger Bands', paramNames: ['Length', 'Std dev'], lines: ['upper', 'middle', 'lower'], pane: 'price' },
  { id: 'rsi', name: 'RSI', paramNames: ['Length'], lines: ['value'], pane: 'own' },
  { id: 'macd', name: 'MACD', paramNames: ['Fast', 'Slow', 'Signal'], lines: ['macd', 'signal'], pane: 'own' },
]

export const DEFAULT_SETTINGS: IndicatorSettings = {
  sma20: { on: true, params: [20] },
  sma50: { on: true, params: [50] },
  sma200: { on: false, params: [200] },
  ema9: { on: false, params: [9] },
  ema21: { on: false, params: [21] },
  vwap: { on: false, params: [] },
  bb: { on: false, params: [20, 2] },
  rsi: { on: false, params: [14] },
  macd: { on: false, params: [12, 26, 9] },
}

/** A label like "SMA 20", "MACD 12 26 9", "VWAP". */
export function indicatorLabel(id: IndicatorId, setting: IndicatorSetting): string {
  const info = INDICATORS.find((i) => i.id === id)!
  return [info.name === 'Bollinger Bands' ? 'BB' : info.name, ...setting.params].join(' ')
}

/** Lines (and a histogram for MACD) to draw for one indicator. */
export interface IndicatorOutput {
  lines: { key: string; values: Values }[]
  histogram?: Values
}

export function compute(id: IndicatorId, params: number[], bars: Bar[]): IndicatorOutput {
  switch (id) {
    case 'sma20':
    case 'sma50':
    case 'sma200':
      return { lines: [{ key: 'value', values: sma(close(bars), params[0]) }] }
    case 'ema9':
    case 'ema21':
      return { lines: [{ key: 'value', values: ema(close(bars), params[0]) }] }
    case 'vwap':
      return { lines: [{ key: 'value', values: vwap(bars) }] }
    case 'bb': {
      const b = bollinger(bars, params[0], params[1])
      return {
        lines: [
          { key: 'upper', values: b.upper },
          { key: 'middle', values: b.middle },
          { key: 'lower', values: b.lower },
        ],
      }
    }
    case 'rsi':
      return { lines: [{ key: 'value', values: rsi(bars, params[0]) }] }
    case 'macd': {
      const m = macd(bars, params[0], params[1], params[2])
      return {
        lines: [
          { key: 'macd', values: m.macd },
          { key: 'signal', values: m.signal },
        ],
        histogram: m.histogram,
      }
    }
  }
}

/** Keep only sane settings (from storage or input): whole lengths 1-500, std dev 0.5-5. */
export function sanitize(raw: unknown): IndicatorSettings {
  const out = structuredClone(DEFAULT_SETTINGS)
  if (!raw || typeof raw !== 'object') return out
  for (const id of Object.keys(out) as IndicatorId[]) {
    const s = (raw as Record<string, Partial<IndicatorSetting>>)[id]
    if (!s) continue
    if (typeof s.on === 'boolean') out[id].on = s.on
    if (Array.isArray(s.params) && s.params.length === out[id].params.length) {
      out[id].params = normalizeParams(id, s.params.map((p, i) => clampParam(id, i, p, out[id].params[i])))
    }
  }
  return out
}

export function clampParam(id: IndicatorId, index: number, value: unknown, fallback: number): number {
  const n = Number(value)
  if (!Number.isFinite(n)) return fallback
  if (id === 'bb' && index === 1) return Math.min(5, Math.max(0.5, Math.round(n * 10) / 10))
  return Math.min(500, Math.max(1, Math.round(n)))
}

/** Fix combinations that don't make sense: MACD's fast length must be shorter than its slow one. */
export function normalizeParams(id: IndicatorId, params: number[]): number[] {
  if (id !== 'macd') return params
  let [fast, slow] = params
  if (fast > slow) [fast, slow] = [slow, fast]
  if (fast === slow) {
    if (slow < 500) slow += 1
    else fast -= 1
  }
  return [fast, slow, params[2]]
}
