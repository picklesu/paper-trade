import { describe, expect, it } from 'vitest'
import type { Quote } from './types'
import {
  DEFAULT_COLUMNS,
  high52,
  moveSymbol,
  nextSort,
  rangePosition,
  sanitizeColumns,
  sanitizeSort,
  sortRows,
  toggleColumn,
  type Row,
} from './watchlist'

function quote(symbol: string, last: number, change_pct: number, extra: Partial<Quote> = {}): Quote {
  return {
    symbol,
    name: symbol,
    last,
    bid: last - 0.01,
    ask: last + 0.01,
    open: last,
    high: last + 1,
    low: last - 1,
    prev_close: last,
    change: 0,
    change_pct,
    volume: 1000,
    bid_size: 100,
    ask_size: 200,
    ...extra,
  }
}

const rows: Row[] = [
  { symbol: 'MSFT', quote: quote('MSFT', 400, 1.5), qty: 0 },
  { symbol: 'AAPL', quote: quote('AAPL', 230, -0.4), qty: 10 },
  { symbol: 'XOM', qty: 0 }, // no quote yet
  { symbol: 'SPY', quote: quote('SPY', 580, 0.2), qty: 0 },
]
const order = (r: Row[]) => r.map((x) => x.symbol)

describe('watchlist sorting', () => {
  it('keeps the list order without a sort', () => {
    expect(order(sortRows(rows, null))).toEqual(['MSFT', 'AAPL', 'XOM', 'SPY'])
  })

  it('sorts by symbol or a column, rows without a value last', () => {
    expect(order(sortRows(rows, { key: 'symbol', dir: 1 }))).toEqual(['AAPL', 'MSFT', 'SPY', 'XOM'])
    expect(order(sortRows(rows, { key: 'change_pct', dir: -1 }))).toEqual(['MSFT', 'SPY', 'AAPL', 'XOM'])
    expect(order(sortRows(rows, { key: 'change_pct', dir: 1 }))).toEqual(['AAPL', 'SPY', 'MSFT', 'XOM'])
    expect(order(sortRows(rows, { key: 'position', dir: -1 }))[0]).toBe('AAPL')
  })

  it('cycles a header: first direction, reversed, then the list order', () => {
    const a = nextSort(null, 'last')
    expect(a).toEqual({ key: 'last', dir: -1 })
    const b = nextSort(a, 'last')
    expect(b).toEqual({ key: 'last', dir: 1 })
    expect(nextSort(b, 'last')).toBeNull()
    expect(nextSort(b, 'symbol')).toEqual({ key: 'symbol', dir: 1 })
  })
})

describe('watchlist helpers', () => {
  it('moves a symbol before another or to the end', () => {
    const list = ['A', 'B', 'C', 'D']
    expect(moveSymbol(list, 'D', 'B')).toEqual(['A', 'D', 'B', 'C'])
    expect(moveSymbol(list, 'A', 'C')).toEqual(['B', 'A', 'C', 'D'])
    expect(moveSymbol(list, 'B', null)).toEqual(['A', 'C', 'D', 'B'])
    expect(moveSymbol(list, 'B', 'B')).toBe(list)
    expect(moveSymbol(list, 'B', 'C')).toBe(list) // dropped on the row below: no change
    expect(moveSymbol(list, 'D', null)).toBe(list)
    expect(moveSymbol(list, 'Z', 'A')).toBe(list)
  })

  it('keeps columns in menu order and drops unknown saved ones', () => {
    expect(toggleColumn(DEFAULT_COLUMNS, 'bid')).toEqual(['last', 'change_pct', 'spark', 'bid'])
    expect(toggleColumn(['volume', 'last'], 'change')).toEqual(['last', 'change', 'volume'])
    expect(toggleColumn(DEFAULT_COLUMNS, 'spark')).toEqual(['last', 'change_pct'])
    expect(sanitizeColumns(['last', 'bogus', 'last', 3])).toEqual(['last'])
    expect(sanitizeColumns('x')).toBeNull()
    expect(sanitizeColumns(['chg'])).toBeNull() // nothing usable: the defaults
    expect(sanitizeColumns([])).toEqual([]) // all turned off on purpose
    expect(sanitizeSort({ key: 'last', dir: -1 })).toEqual({ key: 'last', dir: -1 })
    expect(sanitizeSort({ key: 'nope', dir: 1 })).toBeNull()
  })

  it('folds today into the 52-week range and places the last price in the day range', () => {
    const q = quote('SPY', 600, 0, { high: 610, low: 590 })
    expect(high52({ symbol: 'SPY', quote: q, stats: { high_52w: 605, low_52w: 500, avg_volume: 1 }, qty: 0 })).toBe(610)
    expect(rangePosition(q)).toBe(0.5)
    expect(rangePosition({ ...q, high: 600, low: 600 })).toBe(0.5)
  })
})
