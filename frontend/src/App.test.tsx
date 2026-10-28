import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrictMode } from 'react'
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
    clock: { time: '2026-01-05T15:30:00Z', speed: 1, paused: false, speeds: [1, 10, 60, 600], origin: '2026-01-05T14:30:00Z', session_open: '2026-01-05T14:30:00Z', timeline: 0, building_history: false },
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

/** A workspace panel's content, by title (inactive tabs aren't rendered until shown). */
function panel(name: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-panel="${name}"]`)
  if (!el) throw new Error(`panel ${name} is not shown`)
  return el
}
const hasPanel = (name: string) => document.querySelector(`[data-panel="${name}"]`) !== null

/** Click a workspace tab by its title. */
function openTab(title: string | RegExp) {
  const tab = screen.getAllByText(title).find((el) => el.closest('.dv-tab'))?.closest('.dv-tab')
  if (!tab) throw new Error(`no tab ${title}`)
  // dockview activates a tab on a primary-button pointer press.
  fireEvent.pointerDown(tab, { button: 0 })
  fireEvent.click(tab)
}

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('lays out the default workspace before data arrives', () => {
    render(<App />)
    for (const name of ['Watchlist', 'Chart', 'Order Entry', 'Positions']) {
      expect(panel(name)).toBeInTheDocument()
    }
    // Orders and Activity are tabs behind Positions.
    for (const title of ['Orders', 'Activity']) {
      expect(screen.getAllByText(title).some((el) => el.closest('.dv-tab'))).toBe(true)
    }
    expect(within(screen.getByRole('banner')).getByRole('status')).toHaveTextContent('Connecting')
    expect(panel('Watchlist')).toHaveTextContent('Waiting for market data')
  })

  it('shows streamed quotes, clock and regime', () => {
    render(<App />)
    push(tick({ regime: 'volatile' }))

    expect(within(screen.getByRole('banner')).getByRole('status')).toHaveTextContent('Connected')
    const watchlist = panel('Watchlist')
    expect(within(watchlist).getByText('SPY')).toBeInTheDocument()
    expect(within(watchlist).getByText('+0.34%')).toHaveClass('up')
    expect(within(watchlist).getByText('-0.87%')).toHaveClass('down')
    // 15:30 UTC is 10:30 in New York in January.
    expect(screen.getByText('10:30:00')).toBeInTheDocument()
    expect(screen.getByText('Volatile')).toBeInTheDocument()
  })

  it('shows account totals in the top bar and positions in their panel', () => {
    render(<App />)
    push(tick())
    const banner = screen.getByRole('banner')
    expect(within(banner).getByText('$100,000.00')).toBeInTheDocument()
    expect(within(banner).getByText('$94,180.00')).toBeInTheDocument()
    expect(within(banner).getByText('+$20.00')).toHaveClass('up')
    expect(within(panel('Positions')).getByText('$5,820.00')).toBeInTheDocument()
    expect(within(panel('Positions')).getByText('+$20.00 (+0.34%)')).toHaveClass('up')
    expect(screen.getAllByText('Positions (1)').some((el) => el.closest('.dv-tab'))).toBe(true)
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
    openTab('Orders (1 working)')
    await waitFor(() => expect(hasPanel('Orders')).toBe(true))
    const orders = panel('Orders')
    expect(within(orders).getByText('Limit 570.00')).toBeInTheDocument()
    expect(within(orders).getByText('Filled at 580.03')).toBeInTheDocument()
    fireEvent.click(within(orders).getByRole('button', { name: 'Cancel order 2' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/orders/2', expect.objectContaining({ method: 'DELETE' }))

    // Filters narrow the list.
    fireEvent.click(within(orders).getByRole('button', { name: 'Filled (1)' }))
    expect(within(orders).queryByText('Limit 570.00')).not.toBeInTheDocument()
    expect(within(orders).getByText('Filled at 580.03')).toBeInTheDocument()

    openTab('Activity')
    await waitFor(() => expect(hasPanel('Activity')).toBe(true))
    expect(within(panel('Activity')).getByText('-$5,800.30')).toBeInTheDocument()
  })

  it('selecting a watchlist row updates the chart and order entry', () => {
    render(<App />)
    push(tick())
    expect(panel('Chart')).toHaveTextContent('SPY Inc')

    fireEvent.click(within(panel('Watchlist')).getByText('AAPL'))
    expect(panel('Chart')).toHaveTextContent('AAPL Inc')
    expect(panel('Chart')).toHaveTextContent('228.00')
    expect(panel('Order Entry')).toHaveTextContent('AAPL')
  })

  it('speed and pause buttons call the clock API', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    push(tick())

    fireEvent.click(screen.getByRole('button', { name: '60×' }))
    fireEvent.click(screen.getByRole('button', { name: 'Pause market' }))

    const clockCalls = fetchMock.mock.calls.filter(([url]) => url === '/api/clock')
    expect(clockCalls).toHaveLength(2)
    expect(JSON.parse(clockCalls[0][1].body)).toEqual({ speed: 60 })
    expect(JSON.parse(clockCalls[1][1].body)).toEqual({ paused: true })
  })

  it('shows the resume button while paused', () => {
    render(<App />)
    push(tick({ clock: { time: '2026-01-05T15:30:00Z', speed: 10, paused: true, speeds: [1, 10, 60, 600], origin: '2026-01-05T14:30:00Z', session_open: '2026-01-05T14:30:00Z', timeline: 0, building_history: false } }))
    expect(screen.getByRole('button', { name: 'Resume market' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '10×' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('workspace', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('the Panels menu hides and restores panels', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    const menu = screen.getByRole('menu', { name: 'Panels' })
    const watchlist = within(menu).getByRole('menuitemcheckbox', { name: /Watchlist/ })
    expect(watchlist).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(watchlist)
    expect(hasPanel('Watchlist')).toBe(false)
    expect(within(menu).getByRole('menuitemcheckbox', { name: /Watchlist/ })).toHaveAttribute('aria-checked', 'false')

    fireEvent.click(within(menu).getByRole('menuitemcheckbox', { name: /Watchlist/ }))
    expect(panel('Watchlist')).toBeInTheDocument()
  })

  it('pops a panel out as a floating window and docks it back', () => {
    render(<App />)
    const floatButtons = () => screen.getAllByRole('button', { name: 'Float panel' })
    const before = floatButtons().length
    fireEvent.click(floatButtons()[0])
    expect(screen.getAllByRole('button', { name: 'Dock panel' })).toHaveLength(1)
    expect(document.querySelectorAll('.dv-resize-container')).toHaveLength(1) // dockview's floating window
    fireEvent.click(screen.getByRole('button', { name: 'Dock panel' }))
    expect(screen.queryByRole('button', { name: 'Dock panel' })).not.toBeInTheDocument()
    expect(floatButtons()).toHaveLength(before)
  })

  it('a panel reopened while the chart floats docks in the grid, not inside the floating window', () => {
    render(<App />)
    const chartGroup = panel('Chart').closest('.dv-groupview')!
    fireEvent.click(within(chartGroup as HTMLElement).getByRole('button', { name: 'Float panel' }))
    expect(panel('Chart').closest('.dv-resize-container')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Watchlist/ }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Watchlist/ }))
    expect(panel('Watchlist').closest('.dv-resize-container')).toBeNull()
    expect(panel('Chart').closest('.dv-resize-container')).not.toBeNull()
  })

  it('docking a floating panel returns it to its usual side', () => {
    render(<App />)
    const group = (name: string) => panel(name).closest('.dv-groupview') as HTMLElement
    fireEvent.click(within(group('Watchlist')).getByRole('button', { name: 'Float panel' }))
    fireEvent.click(within(group('Watchlist')).getByRole('button', { name: 'Dock panel' }))
    expect(panel('Watchlist').closest('.dv-resize-container')).toBeNull()
    // Left of the chart again: earlier in the grid than the chart.
    const order = group('Watchlist').compareDocumentPosition(group('Chart'))
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('saves the layout immediately when the page is closing', () => {
    render(<App />)
    localStorage.clear()
    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Activity/ }))
    window.dispatchEvent(new Event('pagehide')) // before the delayed save
    const saved = localStorage.getItem('paper-trade.layout.v1') ?? ''
    expect(saved).toContain('"positions"')
    expect(saved).not.toContain('"activity"')
  })

  it('remembers the layout across reloads, and Reset layout restores the default', async () => {
    const { unmount } = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Order Entry/ }))
    // The layout is saved shortly after it changes.
    await waitFor(() => {
      const saved = localStorage.getItem('paper-trade.layout.v1') ?? ''
      expect(saved).toContain('"watchlist"')
      expect(saved).not.toContain('"ticket"')
    })
    unmount()

    render(<App />)
    expect(panel('Watchlist')).toBeInTheDocument()
    expect(hasPanel('Order Entry')).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Reset layout/ }))
    expect(panel('Order Entry')).toBeInTheDocument()
  })

  it('saves the layout under StrictMode too, as the real page runs', async () => {
    const { unmount } = render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Activity/ }))
    await waitFor(() => expect(localStorage.getItem('paper-trade.layout.v1') ?? '').toContain('"positions"'))
    expect(localStorage.getItem('paper-trade.layout.v1')).not.toContain('"activity"')
    unmount()
  })

  it('ignores a corrupted saved layout', () => {
    localStorage.setItem('paper-trade.layout.v1', '{"grid": "nonsense"}')
    render(<App />)
    expect(panel('Chart')).toBeInTheDocument()
    expect(panel('Order Entry')).toBeInTheDocument()
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
