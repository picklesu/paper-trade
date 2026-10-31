import { describe, expect, it } from 'vitest'
import { isIndex, matchSymbols } from './symbols'

const SYMBOLS = [
  { symbol: 'AMD', name: 'Advanced Micro Devices', sector: null },
  { symbol: 'AMZN', name: 'Amazon.com', sector: null },
  { symbol: 'AAPL', name: 'Apple', sector: null },
  { symbol: 'MSFT', name: 'Microsoft', sector: null },
  { symbol: 'SPY', name: 'S&P 500 ETF', sector: null },
]

describe('matchSymbols', () => {
  it('ranks the exact symbol, then symbol prefixes, then name words, then names containing it', () => {
    expect(matchSymbols(SYMBOLS, 'amd').map((s) => s.symbol)).toEqual(['AMD'])
    expect(matchSymbols(SYMBOLS, 'a').map((s) => s.symbol)).toEqual(['AAPL', 'AMD', 'AMZN'])
    expect(matchSymbols(SYMBOLS, 'micro').map((s) => s.symbol)).toEqual(['AMD', 'MSFT'])
    expect(matchSymbols(SYMBOLS, 'soft').map((s) => s.symbol)).toEqual(['MSFT'])
    expect(matchSymbols(SYMBOLS, 'etf').map((s) => s.symbol)).toEqual(['SPY'])
  })

  it('returns nothing for blank text and respects the limit', () => {
    expect(matchSymbols(SYMBOLS, '  ')).toEqual([])
    expect(matchSymbols(SYMBOLS, 'a', 2)).toHaveLength(2)
  })
})

describe('isIndex', () => {
  it('knows the VIX is an index and stocks are not', () => {
    expect(isIndex('VIX')).toBe(true)
    expect(isIndex('SPY')).toBe(false)
  })
})
