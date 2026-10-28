import { describe, expect, it } from 'vitest'
import type { Bar } from './bars'
import { bollinger, compute, DEFAULT_SETTINGS, ema, indicatorLabel, macd, rsi, sanitize, sma, vwap } from './indicators'

const bars = (closes: number[], start = Date.parse('2026-01-07T14:30:00Z') / 1000, step = 60): Bar[] =>
  closes.map((c, i) => [start + i * step, c, c, c, c, 100])

// StockCharts' worked RSI example ("RSI" in ChartSchool).
const RSI_CLOSES = [
  44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.1, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.0, 46.03,
  46.41, 46.22, 45.64,
]

describe('sma', () => {
  it('averages the last n values', () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4])
  })
})

describe('ema', () => {
  it('seeds with the SMA, then smooths with 2/(n+1)', () => {
    const out = ema([1, 2, 3, 4, 5], 3)
    expect(out.slice(0, 3)).toEqual([null, null, 2])
    expect(out[3]).toBeCloseTo(4 * 0.5 + 2 * 0.5) // 3
    expect(out[4]).toBeCloseTo(5 * 0.5 + 3 * 0.5) // 4
  })

  it('skips leading nulls (for smoothing another indicator)', () => {
    expect(ema([null, null, 2, 4, 6], 2)).toEqual([null, null, null, 3, 5])
  })
})

describe('rsi', () => {
  it("matches Wilder's RSI on StockCharts' example", () => {
    const out = rsi(bars(RSI_CLOSES), 14)
    expect(out.slice(0, 14).every((v) => v === null)).toBe(true)
    // Exact Wilder values; StockCharts' table rounds intermediate averages
    // (70.53, 66.32, 66.55, 69.41, 66.36, 57.97), so it agrees within 0.1.
    const expected = [70.46, 66.25, 66.48, 69.35, 66.29, 57.92]
    const published = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]
    out.slice(14).forEach((v, i) => {
      expect(v).toBeCloseTo(expected[i], 2)
      expect(Math.abs(v! - published[i])).toBeLessThan(0.1)
    })
  })

  it('is 100 with only gains and stays within 0..100', () => {
    expect(rsi(bars([1, 2, 3, 4, 5]), 3).at(-1)).toBe(100)
    const noisy = rsi(bars(Array.from({ length: 200 }, (_, i) => 100 + Math.sin(i) * 5 + i * 0.1)), 14)
    const values = noisy.filter((v) => v !== null) as number[]
    expect(values.length).toBeGreaterThan(150)
    expect(Math.min(...values)).toBeGreaterThanOrEqual(0)
    expect(Math.max(...values)).toBeLessThanOrEqual(100)
  })
})

describe('bollinger', () => {
  it('is the SMA ± k population standard deviations', () => {
    const b = bollinger(bars([2, 4, 4, 4, 5, 5, 7, 9]), 8, 2)
    // Classic example: mean 5, population sd 2.
    expect(b.middle.at(-1)).toBe(5)
    expect(b.upper.at(-1)).toBeCloseTo(9)
    expect(b.lower.at(-1)).toBeCloseTo(1)
    expect(b.upper.slice(0, 7).every((v) => v === null)).toBe(true)
  })
})

describe('macd', () => {
  it('is EMA(fast) − EMA(slow), with the signal as the EMA of that', () => {
    const closes = Array.from({ length: 80 }, (_, i) => 100 + Math.sin(i / 5) * 4 + i * 0.05)
    const m = macd(bars(closes), 12, 26, 9)
    const fast = ema(closes, 12)
    const slow = ema(closes, 26)
    expect(m.macd[24]).toBeNull()
    expect(m.macd[25]).toBeCloseTo(fast[25]! - slow[25]!)
    expect(m.signal[25 + 7]).toBeNull()
    expect(m.signal[25 + 8]).toBeCloseTo(m.macd.slice(25, 34).reduce((a, b) => a! + b!, 0)! / 9)
    expect(m.histogram[50]).toBeCloseTo(m.macd[50]! - m.signal[50]!)
  })
})

describe('vwap', () => {
  it('weights typical price by volume and restarts each session', () => {
    const day1 = Date.parse('2026-01-07T14:30:00Z') / 1000
    const day2 = Date.parse('2026-01-08T14:30:00Z') / 1000
    const b: Bar[] = [
      [day1, 10, 12, 8, 10, 100], // typical 10
      [day1 + 60, 10, 14, 10, 12, 300], // typical 12
      [day2, 50, 51, 49, 50, 10], // new session: typical 50
    ]
    const out = vwap(b)
    expect(out[0]).toBeCloseTo(10)
    expect(out[1]).toBeCloseTo((10 * 100 + 12 * 300) / 400)
    expect(out[2]).toBeCloseTo(50)
  })
})

describe('settings', () => {
  it('labels indicators with their lengths', () => {
    expect(indicatorLabel('macd', DEFAULT_SETTINGS.macd)).toBe('MACD 12 26 9')
    expect(indicatorLabel('bb', DEFAULT_SETTINGS.bb)).toBe('BB 20 2')
    expect(indicatorLabel('vwap', DEFAULT_SETTINGS.vwap)).toBe('VWAP')
  })

  it('sanitizes stored settings', () => {
    const s = sanitize({ sma20: { on: false, params: [9999] }, bb: { on: true, params: [20, 0.1] }, rsi: { params: ['x'] }, nope: 1 })
    expect(s.sma20).toEqual({ on: false, params: [500] })
    expect(s.bb).toEqual({ on: true, params: [20, 0.5] })
    expect(s.rsi).toEqual(DEFAULT_SETTINGS.rsi)
    expect(sanitize('garbage')).toEqual(DEFAULT_SETTINGS)
  })

  it('computes every indicator to the length of the bars', () => {
    const b = bars(Array.from({ length: 60 }, (_, i) => 100 + i))
    for (const id of Object.keys(DEFAULT_SETTINGS) as (keyof typeof DEFAULT_SETTINGS)[]) {
      const out = compute(id, DEFAULT_SETTINGS[id].params, b)
      for (const line of out.lines) expect(line.values).toHaveLength(60)
    }
  })
})

describe('review fixes', () => {
  it('a flat market has a neutral RSI of 50', () => {
    expect(rsi(bars(Array(20).fill(100)), 14).at(-1)).toBe(50)
  })

  it("MACD's fast length is always shorter than its slow one", async () => {
    const { normalizeParams } = await import('./indicators')
    expect(normalizeParams('macd', [26, 12, 9])).toEqual([12, 26, 9])
    expect(normalizeParams('macd', [12, 12, 9])).toEqual([12, 13, 9])
    expect(normalizeParams('macd', [500, 500, 9])).toEqual([499, 500, 9])
    expect(normalizeParams('rsi', [14])).toEqual([14])
    expect(sanitize({ macd: { on: true, params: [30, 10, 9] } }).macd.params).toEqual([10, 30, 9])
  })
})
