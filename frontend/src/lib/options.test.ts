import { describe, expect, it } from 'vitest'
import { expirationLabel, pickExpiration, priceRow, sanitizeChainColumns, strikeWindow, toggleChainColumn } from './options'
import type { Expiration } from './types'

const list: Expiration[] = [
  { date: '2026-11-02', kind: 'daily', days: 0 },
  { date: '2026-11-06', kind: 'weekly', days: 4 },
  { date: '2026-11-20', kind: 'monthly', days: 18 },
  { date: '2027-01-15', kind: 'leaps', days: 74 },
]

describe('option chain helpers', () => {
  it('shows strikes centered on the price', () => {
    const strikes = [90, 95, 100, 105, 110, 115, 120]
    expect(strikeWindow(strikes, 101, 4)).toEqual([1, 5]) // 95 100 | 105 110
    expect(strikeWindow(strikes, 89, 4)).toEqual([0, 4])
    expect(strikeWindow(strikes, 130, 4)).toEqual([3, 7])
    expect(strikeWindow(strikes, 101, 0)).toEqual([0, 7])
    expect(priceRow(strikes, 101)).toBe(3)
    expect(priceRow(strikes, 100)).toBe(2)
    expect(priceRow(strikes, 130)).toBe(7)
  })

  it('keeps the picked expiration, or the next one, or the first a week out', () => {
    expect(pickExpiration(list, '2026-11-20')).toBe('2026-11-20')
    expect(pickExpiration(list, '2026-11-13')).toBe('2026-11-20') // gone or another symbol's: the next one
    expect(pickExpiration(list, null)).toBe('2026-11-20')
    expect(pickExpiration(list, '2028-01-01')).toBe('2026-11-20')
    expect(pickExpiration([], null)).toBeNull()
  })

  it('labels expirations with days left and kind', () => {
    expect(expirationLabel(list[0])).toBe("Nov 2 '26 (today) D")
    expect(expirationLabel(list[2])).toBe("Nov 20 '26 (18d) M")
  })

  it('keeps columns in order and ignores unusable saved ones', () => {
    expect(toggleChainColumn(['bid', 'ask'], 'delta')).toEqual(['bid', 'ask', 'delta'])
    expect(toggleChainColumn(['oi', 'bid'], 'iv')).toEqual(['bid', 'iv', 'oi'])
    expect(toggleChainColumn(['iv'], 'iv')).toEqual(['iv']) // the last column stays
    expect(sanitizeChainColumns(['bogus'])).toBeNull()
    expect(sanitizeChainColumns([])).toBeNull()
    expect(sanitizeChainColumns(['iv', 'iv'])).toEqual(['iv'])
  })
})
