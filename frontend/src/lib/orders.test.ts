import { describe, expect, it } from 'vitest'
import { describeOrder, statusText } from './orders'
import type { Order } from './types'

const dividend = {
  id: 7,
  symbol: 'KO',
  side: 'buy',
  qty: 100,
  type: 'dividend',
  tif: 'day',
  limit_price: null,
  stop_price: null,
  created_at: '2026-03-18T20:00:00Z',
  status: 'filled',
  triggered_at: null,
  filled_at: '2026-03-18T20:00:00Z',
  fill_price: 0.52,
  closed_at: null,
  reason: 'Dividend, ex-date 2026-03-13',
  replaces: null,
  effect: null,
} as Order

describe('dividend records', () => {
  it('say what was paid a share, not that something was bought', () => {
    expect(statusText(dividend)).toBe('Paid 0.52 a share')
    expect(describeOrder(dividend)).toEqual({ text: 'Dividend on 100 KO: 0.52 a share', tone: 'ok' })
  })
})
