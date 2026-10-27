import { describe, expect, it } from 'vitest'
import { direction, marketDateTime, percent, price, shiftInputDays, signed, toMarketInput, volume } from './format'

describe('format', () => {
  it('formats prices with two decimals and separators', () => {
    expect(price(1234.5)).toBe('1,234.50')
  })

  it('signs changes', () => {
    expect(signed(1.5)).toBe('+1.50')
    expect(signed(-1.5)).toBe('-1.50')
    expect(signed(0)).toBe('0.00')
    expect(percent(0.34)).toBe('+0.34%')
    expect(percent(-0.004)).toBe('0.00%')
    expect(percent(-0)).toBe('0.00%')
  })

  it('abbreviates volume', () => {
    expect(volume(950)).toBe('950')
    expect(volume(12_300)).toBe('12.3K')
    expect(volume(1_500_000)).toBe('1.50M')
    expect(volume(2_000_000_000)).toBe('2.00B')
  })

  it('classifies direction', () => {
    expect(direction(1)).toBe('up')
    expect(direction(-1)).toBe('down')
    expect(direction(0)).toBe('flat')
    expect(direction(-0.004)).toBe('flat')
  })
})

describe('market time inputs', () => {
  it('converts to New York wall-clock input format', () => {
    // 15:30 UTC in January is 10:30 in New York.
    expect(toMarketInput('2026-01-05T15:30:00Z')).toBe('2026-01-05T10:30')
    // 14:30 UTC in July (daylight time) is 10:30 in New York.
    expect(toMarketInput('2026-07-06T14:30:00Z')).toBe('2026-07-06T10:30')
  })

  it('shifts by whole days across month ends', () => {
    expect(shiftInputDays('2026-01-30T10:30', 7)).toBe('2026-02-06T10:30')
    expect(shiftInputDays('2026-03-01T09:45', -1)).toBe('2026-02-28T09:45')
    expect(shiftInputDays('', 1)).toBe('')
  })

  it('formats a readable market date and time', () => {
    expect(marketDateTime('2026-01-05T15:30:00Z')).toBe('Mon, Jan 5, 2026, 10:30 AM ET')
  })
})
