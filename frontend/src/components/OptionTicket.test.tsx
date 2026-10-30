import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { contractLabel, occSymbol, optionAction, parseOcc } from '../lib/options'
import type { Account, ChainSide, OptionChain, OrderPreview, Quote } from '../lib/types'
import { OptionChain as ChainView } from './OptionChain'
import { OrderTicket } from './OrderTicket'
import { Positions } from './AccountViews'

const quote = { symbol: 'AAPL', name: 'Apple', last: 101, bid: 100.99, ask: 101.01, open: 100, high: 102, low: 99, prev_close: 100, change: 1, change_pct: 1, volume: 1000, bid_size: 100, ask_size: 100 } as Quote
const strikes = [95, 100, 105]
const side = (base: number): ChainSide => {
  const v = (f: (i: number) => number) => strikes.map((_, i) => f(i))
  return { bid: v((i) => base + i), ask: v((i) => base + i + 0.1), bid_size: v(() => 5), ask_size: v(() => 7), mark: v((i) => base + i + 0.05), iv: v(() => 0.3), delta: v(() => 0.5), gamma: v(() => 0.01), theta: v(() => -0.05), vega: v(() => 0.1), rho: v(() => 0.02), volume: v(() => 10), oi: v(() => 100) }
}
const chain: OptionChain = {
  symbol: 'AAPL', expiration: '2026-11-20', kind: 'monthly', days: 21, years: 0.06, spot: 101, rate: 0.04, atm_iv: 0.3, expected_move: 7,
  earnings: false, calibration: { as_of: null, skew: null, premium: 1.12, earnings: null, fit_error: null }, strikes, call: side(4), put: side(2),
}
const account: Account = { starting_cash: 100_000, cash: 100_000, buying_power: 100_000, net_liq: 100_000, day_pnl: 0, realized: 0, positions: [] }
const symbol = 'AAPL261120C00100000'
const preview: OrderPreview = {
  symbol, side: 'buy', qty: 1, type: 'limit', tif: 'day', limit_price: 5.1, stop_price: null, fills_now: false, estimated_price: 5.1, estimated_total: 510,
  commission: 0, buying_power_before: 100_000, buying_power_after: 99_490, cash_before: 100_000, cash_after: 99_490, position_before: 0, position_after: 1,
  avg_cost_after: 5.1, estimated_realized: null, warnings: [], effect: 'open', breakeven: 105.1, max_loss: 510, max_gain: null, collateral: 0, shares_locked: 0,
  option: { underlying: 'AAPL', expiration: '2026-11-20', right: 'call', strike: 100, label: "AAPL Nov 20 '26 100 Call" },
}

function server() {
  const fn = vi.fn(async (url: string) => {
    const body = url.startsWith('/api/options/expirations') ? [{ date: '2026-11-20', kind: 'monthly', days: 21 }] : url.startsWith('/api/options/exercise/preview') ? null : preview
    return { ok: true, status: 200, json: async () => body }
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('option trading', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('names contracts the way the server does', () => {
    expect(occSymbol('AAPL', '2026-11-20', 'call', 232.5)).toBe('AAPL261120C00232500')
    expect(parseOcc('AAPL261120P00232500')).toEqual({ underlying: 'AAPL', expiration: '2026-11-20', right: 'put', strike: 232.5 })
    expect(parseOcc('AAPL')).toBeNull()
    expect(contractLabel('AAPL', '2026-11-20', 'call', 232.5)).toBe("AAPL Nov 20 '26 232.5 Call")
    expect(optionAction('sell', 0, 'put')).toBe('Sell to open (cash-secured put)')
    expect(optionAction('sell', 2, 'put')).toBe('Sell to close')
    expect(optionAction('buy', -1, 'call')).toBe('Buy to close')
  })

  it('a chain bid or ask trades that contract at that price', () => {
    const onTrade = vi.fn()
    render(<ChainView symbol="AAPL" expirations={[]} expiration="2026-11-20" onExpiration={() => {}} chain={chain} onTrade={onTrade} />)
    fireEvent.click(screen.getByRole('button', { name: 'Buy AAPL 100.00 call at 5.10' }))
    expect(onTrade).toHaveBeenCalledWith(symbol, 'buy', 5.1)
    fireEvent.click(screen.getByRole('button', { name: 'Sell AAPL 105.00 put at 4.00' }))
    expect(onTrade).toHaveBeenLastCalledWith('AAPL261120P00105000', 'sell', 4)
  })

  it('the ticket takes a chain click and reviews the option order', async () => {
    const fetchMock = server()
    render(<OrderTicket quote={quote} account={account} orders={[]} chain={chain} fill={{ symbol, side: 'buy', limit: 5.1, id: 1 }} />)
    expect(screen.getByRole('button', { name: 'Options' })).toHaveAttribute('aria-pressed', 'true')
    expect(await screen.findByText('Bid 5.00 · Ask 5.10')).toBeInTheDocument()
    expect(screen.getByLabelText('Limit price (per share)')).toHaveValue(5.1)
    expect(screen.getByText('Buy to open')).toBeInTheDocument()
    expect(screen.getByText('$510.00')).toBeInTheDocument() // 1 contract x 100 x 5.10
    fireEvent.click(screen.getByRole('button', { name: "Review buy to open 1 AAPL Nov 20 '26 100 Call" }))
    const review = await screen.findByLabelText('Review order')
    expect(review).toHaveTextContent("Buy to open 1 AAPL Nov 20 '26 100 Call")
    expect(review).toHaveTextContent('Breakeven at expiration105.10')
    expect(review).toHaveTextContent('Most you can lose$510.00')
    expect(review).toHaveTextContent('0 → 1 contracts')
    const sent = JSON.parse((fetchMock.mock.calls.find(([u]) => u === '/api/orders/preview') as unknown as [string, RequestInit])[1].body as string)
    expect(sent).toEqual({ symbol, side: 'buy', qty: 1, type: 'limit', tif: 'day', limit_price: 5.1 })
  })

  it('selling a call without holding it is a covered call', async () => {
    server()
    render(<OrderTicket quote={quote} account={account} orders={[]} chain={chain} fill={{ symbol, side: 'sell', limit: 5, id: 2 }} />)
    expect(await screen.findByText('Sell to open (covered call)')).toBeInTheDocument()
  })

  it('positions show options with their Greeks and an Exercise button', () => {
    server()
    const option = { underlying: 'AAPL', expiration: '2026-11-20', right: 'call' as const, strike: 100, label: "AAPL Nov 20 '26 100 Call" }
    const held: Account = {
      ...account,
      collateral: 0,
      positions: [{ symbol, qty: 2, avg_cost: 5, last: 5.05, market_value: 1010, unrealized: 10, unrealized_pct: 1, delta: 100, theta: -10, option }],
    }
    const onSelect = vi.fn()
    render(<Positions account={held} onSelect={onSelect} />)
    const row = screen.getByText(option.label).closest('tr')!
    expect(within(row).getByText('100')).toBeInTheDocument()
    fireEvent.click(row)
    expect(onSelect).toHaveBeenCalledWith('AAPL')
    fireEvent.click(screen.getByRole('button', { name: `Exercise ${option.label}` }))
    expect(screen.getByRole('dialog', { name: `Exercise ${option.label}` })).toBeInTheDocument()
  })
})

describe('the ticket keeps state honest', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('a review is dropped when the symbol changes', async () => {
    server()
    const { rerender } = render(<OrderTicket quote={quote} account={account} orders={[]} chain={chain} fill={{ symbol, side: 'buy', limit: 5.1, id: 1 }} />)
    fireEvent.click(await screen.findByRole('button', { name: /^Review buy to open/ }))
    expect(await screen.findByLabelText('Review order')).toBeInTheDocument()
    rerender(<OrderTicket quote={{ ...quote, symbol: 'MSFT' }} account={account} orders={[]} chain={chain} fill={null} />)
    expect(screen.queryByLabelText('Review order')).not.toBeInTheDocument()
  })

  it('switching to Stock and back keeps the option form as it was', async () => {
    server()
    render(<OrderTicket quote={quote} account={account} orders={[]} chain={chain} fill={{ symbol, side: 'buy', limit: 5.1, id: 1 }} />)
    fireEvent.change(await screen.findByLabelText('Limit price (per share)'), { target: { value: '4.5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Stock' }))
    fireEvent.click(screen.getByRole('button', { name: 'Options' }))
    expect(screen.getByLabelText('Limit price (per share)')).toHaveValue(4.5)
  })
})
