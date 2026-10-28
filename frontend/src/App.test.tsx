import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Order, Quote, TickMessage } from './lib/types'

// Minimal stand-in so tests can push stream messages without a backend.
class FakeWebSocket {
  static last: FakeWebSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  constructor() {
    FakeWebSocket.last = this
  }
  close() {}
}
vi.stubGlobal('WebSocket', FakeWebSocket)

function quote(symbol: string, last: number, prev_close: number): Quote {
  const change = last - prev_close
  return {
    symbol,
    name: `${symbol} Inc`,
    last,
    bid: last - 0.01,
    ask: last + 0.01,
    open: prev_close,
    high: Math.max(last, prev_close),
    low: Math.min(last, prev_close),
    prev_close,
    change,
    change_pct: (change / prev_close) * 100,
    volume: 1_500_000,
  }
}

function order(overrides: Partial<Order>): Order {
  return {
    id: 1,
    symbol: 'SPY',
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
    ...overrides,
  }
}

function tick(overrides: Partial<TickMessage> = {}): TickMessage {
  return {
    type: 'tick',
    clock: { time: '2026-01-05T15:30:00Z', speed: 1, paused: false, speeds: [1, 10, 60, 600], origin: '2026-01-05T14:30:00Z' },
    regime: 'normal',
    quotes: [quote('SPY', 582, 580), quote('AAPL', 228, 230)],
    account: {
      starting_cash: 100_000,
      cash: 94_180,
      buying_power: 94_180,
      net_liq: 100_000,
      day_pnl: 20,
      realized: 0,
      positions: [
        { symbol: 'SPY', qty: 10, avg_cost: 580, last: 582, market_value: 5820, unrealized: 20, unrealized_pct: 0.34 },
      ],
    },
    ...overrides,
  }
}

function push(msg: TickMessage) {
  act(() => {
    FakeWebSocket.last!.onopen?.()
    FakeWebSocket.last!.onmessage?.({ data: JSON.stringify(msg) })
  })
}

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the main panels before data arrives', () => {
    render(<App />)
    for (const name of ['Watchlist', 'Chart', 'Order Entry']) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('status')).toHaveTextContent('Connecting')
    expect(screen.getByRole('region', { name: 'Watchlist' })).toHaveTextContent('Waiting for market data')
  })

  it('switches bottom tabs', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: 'Orders' }))
    expect(screen.getByRole('tab', { name: 'Orders' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('No orders yet')
  })

  it('shows streamed quotes, clock and regime', () => {
    render(<App />)
    push(tick({ regime: 'volatile' }))

    expect(screen.getByRole('status')).toHaveTextContent('Connected')
    const watchlist = screen.getByRole('region', { name: 'Watchlist' })
    expect(within(watchlist).getByText('SPY')).toBeInTheDocument()
    expect(within(watchlist).getByText('+0.34%')).toHaveClass('up')
    expect(within(watchlist).getByText('-0.87%')).toHaveClass('down')
    // 15:30 UTC is 10:30 in New York in January.
    expect(screen.getByText('10:30:00')).toBeInTheDocument()
    expect(screen.getByText('Volatile')).toBeInTheDocument()
  })

  it('shows account totals in the top bar and positions in the tab', () => {
    render(<App />)
    push(tick())
    const banner = screen.getByRole('banner')
    expect(within(banner).getByText('$100,000.00')).toBeInTheDocument()
    expect(within(banner).getByText('$94,180.00')).toBeInTheDocument()
    expect(within(banner).getByText('+$20.00')).toHaveClass('up')
    const positions = screen.getByRole('tabpanel')
    expect(within(positions).getByText('$5,820.00')).toBeInTheDocument()
    expect(within(positions).getByText('+$20.00 (+0.34%)')).toHaveClass('up')
  })

  it('shows orders from the stream with a cancel button for working ones', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    push(tick())
    act(() => {
      FakeWebSocket.last!.onmessage?.({
        data: JSON.stringify({
          type: 'orders',
          orders: [
            order({ id: 2, status: 'working', type: 'limit', limit_price: 570 }),
            order({ id: 1, status: 'filled', fill_price: 580.03, filled_at: '2026-01-05T15:00:00Z' }),
          ],
        }),
      })
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Orders (1 working)' }))
    const panel = screen.getByRole('tabpanel')
    expect(within(panel).getByText('Limit 570.00')).toBeInTheDocument()
    expect(within(panel).getByText('Filled at 580.03')).toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Cancel order 2' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/orders/2', expect.objectContaining({ method: 'DELETE' }))

    // Filters narrow the list.
    fireEvent.click(within(panel).getByRole('button', { name: 'Filled (1)' }))
    expect(within(panel).queryByText('Limit 570.00')).not.toBeInTheDocument()
    expect(within(panel).getByText('Filled at 580.03')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }))
    expect(within(screen.getByRole('tabpanel')).getByText('-$5,800.30')).toBeInTheDocument()
  })

  it('selecting a watchlist row shows its quote detail', () => {
    render(<App />)
    push(tick())
    const chart = screen.getByRole('region', { name: 'Chart' })
    expect(chart).toHaveTextContent('SPY Inc')

    fireEvent.click(screen.getByText('AAPL'))
    expect(chart).toHaveTextContent('AAPL Inc')
    expect(chart).toHaveTextContent('228.00')
  })

  it('speed and pause buttons call the clock API', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    push(tick())

    fireEvent.click(screen.getByRole('button', { name: '60×' }))
    fireEvent.click(screen.getByRole('button', { name: 'Pause market' }))

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ speed: 60 })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ paused: true })
  })

  it('shows the resume button while paused', () => {
    render(<App />)
    push(tick({ clock: { time: '2026-01-05T15:30:00Z', speed: 10, paused: true, speeds: [1, 10, 60, 600], origin: '2026-01-05T14:30:00Z' } }))
    expect(screen.getByRole('button', { name: 'Resume market' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '10×' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('fast-forward progress', () => {
  it('shows progress and disables controls until the next tick', () => {
    render(<App />)
    push(tick())
    act(() => {
      FakeWebSocket.last!.onmessage?.({ data: JSON.stringify({ type: 'progress', done: 10, total: 40 }) })
    })
    expect(screen.getByRole('progressbar')).toHaveTextContent('25%')
    expect(screen.getByRole('button', { name: '60×' })).toBeDisabled()

    push(tick())
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '60×' })).toBeEnabled()
  })
})
