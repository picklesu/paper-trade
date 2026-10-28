import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Account, Order, OrderPreview, Quote } from '../lib/types'
import { OrderTicket } from './OrderTicket'

const quote: Quote = {
  symbol: 'AAPL',
  name: 'Apple',
  last: 230,
  bid: 229.99,
  ask: 230.01,
  open: 229,
  high: 231,
  low: 228,
  prev_close: 229,
  change: 1,
  change_pct: 0.44,
  volume: 1000,
}

const account: Account = {
  starting_cash: 100_000,
  cash: 100_000,
  buying_power: 100_000,
  net_liq: 100_000,
  day_pnl: 0,
  realized: 0,
  positions: [{ symbol: 'AAPL', qty: 4, avg_cost: 200, last: 230, market_value: 920, unrealized: 120, unrealized_pct: 15 }],
}

const preview: OrderPreview = {
  symbol: 'AAPL',
  side: 'buy',
  qty: 10,
  type: 'market',
  tif: 'day',
  limit_price: null,
  stop_price: null,
  fills_now: true,
  estimated_price: 230.01,
  estimated_total: 2300.1,
  commission: 0,
  buying_power_before: 100_000,
  buying_power_after: 97_699.9,
  cash_before: 100_000,
  cash_after: 97_699.9,
  position_before: 4,
  position_after: 14,
  avg_cost_after: 221.43,
  estimated_realized: null,
  warnings: [],
}

function order(overrides: Partial<Order>): Order {
  return {
    id: 1,
    symbol: 'AAPL',
    side: 'buy',
    qty: 10,
    type: 'market',
    tif: 'day',
    limit_price: null,
    stop_price: null,
    created_at: '2026-01-05T15:00:00Z',
    status: 'working',
    triggered_at: null,
    filled_at: null,
    fill_price: null,
    closed_at: null,
    reason: null,
    replaces: null,
    realized: null,
    ...overrides,
  }
}

/** Route fetches by URL: preview and place responses (or errors). */
function server(routes: { preview?: unknown; place?: unknown; error?: string }) {
  const fn = vi.fn(async (url: string) => {
    if (routes.error) return { ok: false, status: 422, json: async () => ({ detail: routes.error }) }
    const body = url.endsWith('/preview') ? routes.preview : routes.place
    return { ok: true, status: 200, json: async () => body }
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

const bodyOf = (fn: ReturnType<typeof server>, url: string) =>
  JSON.parse((fn.mock.calls.find(([u]) => u === url) as unknown as [string, RequestInit])[1].body as string)

describe('OrderTicket', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('reviews before placing, then places the same order', async () => {
    const fetchMock = server({ preview, place: order({ status: 'filled', fill_price: 230.01 }) })
    render(<OrderTicket quote={quote} account={account} orders={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review buy 10 AAPL' }))

    const review = await screen.findByLabelText('Review order')
    expect(review).toHaveTextContent('230.01 (fills now)')
    expect(review).toHaveTextContent('$100,000.00 → $97,699.90')
    expect(review).toHaveTextContent('4 → 14 shares')
    expect(fetchMock).toHaveBeenCalledTimes(1) // only the preview so far

    fireEvent.click(screen.getByRole('button', { name: 'Place order' }))
    expect(await screen.findByText('Bought 10 AAPL at 230.01')).toBeInTheDocument()
    expect(bodyOf(fetchMock, '/api/orders')).toEqual(bodyOf(fetchMock, '/api/orders/preview'))
    expect(bodyOf(fetchMock, '/api/orders')).toEqual({ symbol: 'AAPL', side: 'buy', qty: 10, type: 'market', tif: 'day' })
  })

  it('Edit goes back to the form without placing', async () => {
    const fetchMock = server({ preview })
    render(<OrderTicket quote={quote} account={account} orders={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review buy 10 AAPL' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
    expect(screen.getByLabelText('Quantity (shares)')).toHaveValue(10)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('shows warnings from the preview', async () => {
    server({ preview: { ...preview, warnings: ['Day order: if it hasn\'t filled by 4:00 PM ET today, it expires.'] } })
    render(<OrderTicket quote={quote} account={account} orders={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review buy 10 AAPL' }))
    expect(await screen.findByText(/it expires/)).toBeInTheDocument()
  })

  it('stop limit sends both prices and the time in force', async () => {
    const fetchMock = server({ preview: { ...preview, side: 'sell', type: 'stop_limit' } })
    render(<OrderTicket quote={quote} account={account} orders={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }))
    expect(screen.getByText('Shares owned')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Quantity (shares)'), { target: { value: '4' } })
    fireEvent.change(screen.getByLabelText('Order type'), { target: { value: 'stop_limit' } })
    expect(screen.getByLabelText('Limit price')).toHaveValue(230) // starts at the last price
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '220' } })
    fireEvent.change(screen.getByLabelText('Limit price'), { target: { value: '219.5' } })
    fireEvent.change(screen.getByLabelText('Time in force'), { target: { value: 'gtc' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review sell 4 AAPL' }))
    await screen.findByLabelText('Review order')
    expect(bodyOf(fetchMock, '/api/orders/preview')).toEqual({
      symbol: 'AAPL',
      side: 'sell',
      qty: 4,
      type: 'stop_limit',
      tif: 'gtc',
      stop_price: 220,
      limit_price: 219.5,
    })
  })

  it("shows why an order can't be placed, without a review", async () => {
    server({ error: 'Not enough buying power: this order needs $230,010.00, you have $100,000.00' })
    render(<OrderTicket quote={quote} account={account} orders={[]} />)
    fireEvent.change(screen.getByLabelText('Quantity (shares)'), { target: { value: '1000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review buy 1,000 AAPL' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Not enough buying power'))
    expect(screen.queryByLabelText('Review order')).not.toBeInTheDocument()
  })

  it('the status line disappears when going back in time erases the order', async () => {
    server({ preview: { ...preview, type: 'limit', fills_now: false }, place: order({ id: 12, type: 'limit', limit_price: 225 }) })
    const { rerender } = render(<OrderTicket quote={quote} account={account} orders={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review buy 10 AAPL' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Place order' }))
    await screen.findByText(/Order #12 working/)
    rerender(<OrderTicket quote={quote} account={account} orders={[order({ id: 12, type: 'limit', limit_price: 225 })]} />)
    expect(screen.getByText(/Order #12 working/)).toBeInTheDocument()
    rerender(<OrderTicket quote={quote} account={account} orders={[]} />) // erased by a rewind
    expect(screen.queryByText(/Order #12/)).not.toBeInTheDocument()
  })

  it('the status line follows the order as it fills and through a replace', async () => {
    server({ preview: { ...preview, type: 'limit', fills_now: false }, place: order({ id: 5, type: 'limit', limit_price: 225 }) })
    const { rerender } = render(<OrderTicket quote={quote} account={account} orders={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review buy 10 AAPL' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Place order' }))
    expect(await screen.findByText(/Order #5 working: buy 10 AAPL, Limit 225.00, Day/)).toBeInTheDocument()

    // Replaced by #6, which then fills: the line follows to #6.
    const orders = [
      order({ id: 6, type: 'limit', limit_price: 226, replaces: 5, status: 'filled', fill_price: 226 }),
      order({ id: 5, type: 'limit', limit_price: 225, status: 'canceled', reason: 'Replaced by #6' }),
    ]
    rerender(<OrderTicket quote={quote} account={account} orders={orders} />)
    expect(screen.getByText('Bought 10 AAPL at 226.00')).toBeInTheDocument()
  })
})
