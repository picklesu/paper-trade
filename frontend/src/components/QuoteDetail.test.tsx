import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Quote } from '../lib/types'
import { QuoteDetail } from './QuoteDetail'

const quote: Quote = {
  symbol: 'AAPL',
  name: 'Apple',
  last: 230.5,
  bid: 230.49,
  ask: 230.51,
  open: 229,
  high: 231,
  low: 228,
  prev_close: 229.5,
  change: 1,
  change_pct: 0.44,
  volume: 1000,
  bid_size: 100,
  ask_size: 100,
}

describe('QuoteDetail', () => {
  it('shows only the symbol, name, price and change', () => {
    const onLookup = vi.fn()
    const { container } = render(<QuoteDetail quote={quote} onLookup={onLookup} />)
    // Nothing else: no bid/ask, ranges or volume.
    expect(container.textContent).toBe('AAPLApple230.50+1.00 (+0.44%)')
    fireEvent.click(screen.getByRole('button', { name: 'AAPL' }))
    expect(onLookup).toHaveBeenCalled()
  })
})
