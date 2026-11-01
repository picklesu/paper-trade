import { describe, expect, it } from 'vitest'
import { applyTick, type Bar, bucketStart, fillMarkers, formatBarTime, haltMarkers, mergeBars, RANGES, rangeStart, weekKey } from './bars'
import type { Order } from './types'

const OPEN = Date.parse('2026-01-07T14:30:00Z') / 1000 // Wed 9:30 ET
const at = (min: number, sec = 0) => OPEN + min * 60 + sec

describe('bucketStart', () => {
  it('aligns intraday bars to the 9:30 open', () => {
    expect(bucketStart('1m', at(12, 40), OPEN, null)).toBe(at(12))
    expect(bucketStart('5m', at(12, 40), OPEN, null)).toBe(at(10))
    expect(bucketStart('1h', at(95), OPEN, null)).toBe(at(60)) // 10:30-11:30 bar
  })

  it('uses the session open for daily bars', () => {
    expect(bucketStart('1D', at(200), OPEN, at(-1440))).toBe(OPEN)
  })

  it('keeps the week bar within a week, starts a new one on a new week', () => {
    const monday = Date.parse('2026-01-05T14:30:00Z') / 1000
    expect(bucketStart('1W', at(5), OPEN, monday)).toBe(monday)
    const nextMonday = Date.parse('2026-01-12T14:30:00Z') / 1000
    expect(bucketStart('1W', nextMonday + 60, nextMonday, monday)).toBe(nextMonday)
  })
})

describe('weekKey', () => {
  it('is the Monday of the New York week', () => {
    expect(weekKey(OPEN)).toBe('2026-01-05')
    expect(weekKey(Date.parse('2026-01-11T20:00:00Z') / 1000)).toBe('2026-01-05') // Sunday
  })
})

describe('applyTick', () => {
  const bars: Bar[] = [[at(0), 100, 101, 99, 100.5, 1000]]

  it('updates the current bar', () => {
    expect(applyTick(bars, at(0), 102)).toEqual([[at(0), 100, 102, 99, 102, 1000]])
    expect(applyTick(bars, at(0), 98)).toEqual([[at(0), 100, 101, 98, 98, 1000]])
  })

  it('starts a new bar', () => {
    expect(applyTick(bars, at(1), 100.7)).toEqual([...bars, [at(1), 100.7, 100.7, 100.7, 100.7, 0]])
  })

  it('ignores ticks older than the last bar', () => {
    expect(applyTick(bars, at(-1), 50)).toBe(bars)
  })
})

describe('mergeBars', () => {
  it('joins pages in time order, newer copies winning', () => {
    const older: Bar[] = [[1, 1, 1, 1, 1, 1], [2, 2, 2, 2, 2, 2]]
    const newer: Bar[] = [[2, 9, 9, 9, 9, 9], [3, 3, 3, 3, 3, 3]]
    expect(mergeBars(older, newer).map((b) => b[1])).toEqual([1, 9, 3])
  })
})

describe('fillMarkers', () => {
  const bars: Bar[] = [0, 5, 10].map((m) => [at(m), 1, 1, 1, 1, 1])
  const fill = (id: number, iso: string, side: 'buy' | 'sell', symbol = 'SPY') =>
    ({ id, symbol, side, qty: 10, status: 'filled', filled_at: iso }) as Order

  it('puts each fill on the bar it happened in', () => {
    const t = (m: number) => new Date(at(m) * 1000).toISOString()
    const marks = fillMarkers(
      bars,
      [fill(1, t(7), 'buy'), fill(2, t(12), 'sell'), fill(3, t(3), 'buy', 'AAPL'), fill(4, t(-20), 'buy')],
      'SPY',
    )
    expect(marks).toEqual([
      { time: at(5), side: 'buy', text: 'B 10' },
      { time: at(10), side: 'sell', text: 'S 10' },
    ])
  })
})

describe('ranges and labels', () => {
  it('computes range starts', () => {
    const fiveDay = RANGES.find((r) => r.label === '5D')!
    expect(rangeStart(fiveDay, OPEN, OPEN)).toBe(OPEN - 7 * 86400)
    expect(rangeStart(RANGES[0], at(100), OPEN)).toBe(OPEN)
    expect(rangeStart(RANGES.find((r) => r.label === 'YTD')!, OPEN, OPEN)).toBe(Date.parse('2026-01-01T00:00:00Z') / 1000)
  })

  it('formats bar times in New York time', () => {
    expect(formatBarTime(at(60), '5m')).toBe('Wed, Jan 7, 2026 10:30 AM')
    expect(formatBarTime(OPEN, '1D')).toBe('Wed, Jan 7, 2026')
    expect(formatBarTime(OPEN, '1W')).toBe('Week of Jan 7, 2026')
  })
})

describe('fillMarkers and dividends', () => {
  it('leaves dividend records off the chart', () => {
    const bars: Bar[] = [[1_000, 1, 2, 0.5, 1.5, 10]]
    const order = { symbol: 'KO', status: 'filled', filled_at: new Date(1_500_000).toISOString(), type: 'dividend', side: 'buy', qty: 100, fill_price: 0.5 } as Order
    expect(fillMarkers(bars, [order], 'KO')).toEqual([])
  })
})

describe('haltMarkers', () => {
  const bars: Bar[] = [at(0), at(30), at(60)].map((t) => [t, 1, 2, 0.5, 1.5, 10])
  it('puts each halt on the bar it began in and says when the day was closed', () => {
    const marks = haltMarkers(bars, [
      { start: at(40), resume: at(55), level: 1 },
      { start: at(61), resume: null, level: 3 },
      { start: at(-10), resume: at(5), level: 2 },
    ])
    expect(marks).toEqual([
      { time: at(30), text: 'Halt L1' },
      { time: at(60), text: 'Halt L3 (closed)' },
    ])
    expect(haltMarkers([], [{ start: at(1), resume: null, level: 3 }])).toEqual([])
  })
})
