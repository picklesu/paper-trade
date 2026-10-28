import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Account, Quote } from '../lib/types'
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

function respond(body: unknown, ok = true, status = 200) {
  const fn = vi.fn().mockResolvedValue({ ok, status, json: async () => body })
  vi.stubGlobal('fetch', fn)
  return fn
}

const posted = (fn: ReturnType<typeof respond>) => JSON.parse(fn.mock.calls[0][1].body)

describe('OrderTicket', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('places a market buy and reports the fill', async () => {
    const fetchMock = respond({ id: 1, symbol: 'AAPL', side: 'buy', qty: 10, status: 'filled', fill_price: 230.01 })
    render(<OrderTicket quote={quote} account={account} />)
    expect(screen.getByText('$2,300.10')).toBeInTheDocument() // 10 x ask
    fireEvent.click(screen.getByRole('button', { name: 'Buy 10 AAPL' }))

    expect(await screen.findByText('Bought 10 AAPL at 230.01')).toBeInTheDocument()
    expect(posted(fetchMock)).toEqual({ symbol: 'AAPL', side: 'buy', qty: 10, type: 'market', tif: 'day' })
  })

  it('stop limit asks for both prices and the time in force', async () => {
    const fetchMock = respond({ id: 3, symbol: 'AAPL', side: 'sell', qty: 4, status: 'working' })
    render(<OrderTicket quote={quote} account={account} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }))
    expect(screen.getByText('Shares owned')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Quantity (shares)'), { target: { value: '4' } })
    fireEvent.change(screen.getByLabelText('Order type'), { target: { value: 'stop_limit' } })
    expect(screen.getByLabelText('Limit price')).toHaveValue(230) // starts at the last price
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '220' } })
    fireEvent.change(screen.getByLabelText('Limit price'), { target: { value: '219.5' } })
    fireEvent.change(screen.getByLabelText('Time in force'), { target: { value: 'gtc' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sell 4 AAPL' }))

    expect(await screen.findByText('Order #3 is working')).toBeInTheDocument()
    expect(posted(fetchMock)).toEqual({
      symbol: 'AAPL',
      side: 'sell',
      qty: 4,
      type: 'stop_limit',
      tif: 'gtc',
      stop_price: 220,
      limit_price: 219.5,
    })
  })

  it('shows why the server rejected an order', async () => {
    respond({ detail: 'Not enough buying power: this order needs $230,010.00, you have $100,000.00' }, false, 422)
    render(<OrderTicket quote={quote} account={account} />)
    fireEvent.change(screen.getByLabelText('Quantity (shares)'), { target: { value: '1000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Buy 1,000 AAPL' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Not enough buying power'))
  })
})
