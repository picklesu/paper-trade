import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrictMode } from 'react'
import { DockviewApi } from 'dockview-react'
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

/** Place a panel from the Panels menu: Open, Collapsed, Float or Off. */
function setPanel(name: string, state: 'Open' | 'Collapsed' | 'Float' | 'Off') {
  if (!screen.queryByRole('menu', { name: 'Panels' })) fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
  fireEvent.click(within(screen.getByRole('radiogroup', { name })).getByRole('radio', { name: state }))
}
const stateOf = (name: string) => {
  if (!screen.queryByRole('menu', { name: 'Panels' })) fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
  return within(screen.getByRole('radiogroup', { name })).getByRole('radio', { checked: true }).textContent
}
const strip = () => screen.queryByRole('toolbar', { name: 'Collapsed panels (left)' })
const stripTab = (name: string | RegExp) => within(strip()!).getByRole('button', { name })
const group = (name: string) => panel(name).closest('.dv-groupview') as HTMLElement

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('lays out the default workspace before data arrives: the chart, everything else collapsed on the left', () => {
    render(<App />)
    expect(panel('Chart')).toBeInTheDocument()
    expect(within(strip()!).getAllByRole('button').map((b) => b.textContent)).toEqual(['Watchlist', 'Order Entry', 'Positions', 'Orders', 'Activity'])
    // No tab bars: the Panels menu places panels.
    const headers = [...document.querySelectorAll<HTMLElement>('.dv-tabs-and-actions-container')]
    expect(headers.length).toBeGreaterThan(0)
    for (const header of headers) expect(header.style.display).toBe('none')
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
    // The collapsed panel's tab says how many there are.
    expect(stripTab('Positions, 1 open')).toBeInTheDocument()
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
    expect(stripTab('Orders, 1 working')).toBeInTheDocument()
    setPanel('Orders', 'Open')
    const orders = panel('Orders')
    expect(within(orders).getByText('Limit 570.00')).toBeInTheDocument()
    expect(within(orders).getByText('Filled at 580.03')).toBeInTheDocument()
    fireEvent.click(within(orders).getByRole('button', { name: 'Cancel order 2' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/orders/2', expect.objectContaining({ method: 'DELETE' }))

    // Filters narrow the list.
    fireEvent.click(within(orders).getByRole('button', { name: 'Filled (1)' }))
    expect(within(orders).queryByText('Limit 570.00')).not.toBeInTheDocument()
    expect(within(orders).getByText('Filled at 580.03')).toBeInTheDocument()

    setPanel('Activity', 'Open')
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

  it('the Panels menu opens, collapses, floats and turns off each panel', async () => {
    render(<App />)
    expect(stateOf('Watchlist')).toBe('Collapsed')
    expect(stateOf('Chart')).toBe('Open')

    setPanel('Watchlist', 'Open')
    await waitFor(() => expect(within(strip()!).queryByRole('button', { name: 'Watchlist' })).not.toBeInTheDocument())
    expect(stateOf('Watchlist')).toBe('Open')

    setPanel('Watchlist', 'Float')
    expect(panel('Watchlist').closest('.dv-resize-container')).not.toBeNull() // dockview's floating window
    await waitFor(() => expect(stateOf('Watchlist')).toBe('Float'))

    setPanel('Watchlist', 'Off')
    expect(hasPanel('Watchlist')).toBe(false)
    await waitFor(() => expect(stateOf('Watchlist')).toBe('Off'))

    setPanel('Watchlist', 'Collapsed')
    await waitFor(() => expect(stripTab('Watchlist')).toBeInTheDocument())
    expect(panel('Watchlist').closest('.dv-resize-container')).toBeNull()
  })

  it('docking a floating panel puts it back in the left column', () => {
    render(<App />)
    setPanel('Order Entry', 'Float')
    setPanel('Order Entry', 'Open')
    expect(panel('Order Entry').closest('.dv-resize-container')).toBeNull()
    // Left of the chart: earlier in the grid.
    expect(group('Order Entry').compareDocumentPosition(group('Chart')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a panel opened while the chart floats docks in the grid, not inside the floating window', () => {
    render(<App />)
    setPanel('Chart', 'Float')
    expect(panel('Chart').closest('.dv-resize-container')).not.toBeNull()
    setPanel('Watchlist', 'Off')
    setPanel('Watchlist', 'Open')
    expect(panel('Watchlist').closest('.dv-resize-container')).toBeNull()
    expect(panel('Chart').closest('.dv-resize-container')).not.toBeNull()
  })

  it('while the chart floats, a placeholder keeps its place, so collapsed panels stay on the left', async () => {
    render(<App />)
    setPanel('Chart', 'Float')
    setPanel('Watchlist', 'Float')
    expect(document.querySelector('.chart-space')).not.toBeNull()
    await waitFor(() => expect(within(strip()!).getAllByRole('button').map((b) => b.textContent)).toEqual(['Order Entry', 'Positions', 'Orders', 'Activity']))
    expect(screen.queryByRole('toolbar', { name: /Collapsed panels \((top|bottom)\)/ })).not.toBeInTheDocument()
    // The placeholder isn't a panel you can place.
    expect(screen.queryByRole('radiogroup', { name: '' })).not.toBeInTheDocument()

    // Docking the chart takes the placeholder's place.
    setPanel('Chart', 'Open')
    expect(panel('Chart').closest('.dv-resize-container')).toBeNull()
    await waitFor(() => expect(document.querySelector('.chart-space')).toBeNull())
    expect(group('Order Entry').compareDocumentPosition(group('Chart')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a strip tab peeks at a collapsed panel until a click elsewhere; Open in the menu keeps it', () => {
    render(<App />)
    const tab = stripTab('Watchlist')
    fireEvent.click(tab)
    expect(tab).toHaveAttribute('aria-pressed', 'true')
    expect(group('Watchlist')).toHaveClass('peeking')
    fireEvent.pointerDown(panel('Chart'))
    expect(tab).toHaveAttribute('aria-pressed', 'false')

    // Clicks outside the workspace (the top bar) leave it; the same tab again tucks it away.
    fireEvent.click(tab)
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Panels ▾' }))
    expect(tab).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(tab)
    expect(tab).toHaveAttribute('aria-pressed', 'false')

    // Peeking counts as collapsed; Open pins it.
    fireEvent.click(tab)
    expect(stateOf('Watchlist')).toBe('Collapsed')
    setPanel('Watchlist', 'Open')
    expect(group('Watchlist')).not.toHaveClass('peeking')
    fireEvent.pointerDown(panel('Chart'))
    expect(stateOf('Watchlist')).toBe('Open')
  })

  it('saves collapsed panels as collapsed, even one being peeked at', () => {
    const { unmount } = render(<App />)
    setPanel('Order Entry', 'Open')
    fireEvent.click(stripTab('Watchlist'))
    window.dispatchEvent(new Event('pagehide'))
    unmount()

    render(<App />)
    expect(stripTab('Watchlist')).toHaveAttribute('aria-pressed', 'false')
    expect(within(strip()!).queryByRole('button', { name: 'Order Entry' })).not.toBeInTheDocument()
  })

  it('saves the layout immediately when the page is closing', () => {
    render(<App />)
    localStorage.clear()
    setPanel('Activity', 'Off')
    window.dispatchEvent(new Event('pagehide')) // before the delayed save
    const saved = localStorage.getItem('paper-trade.layout.v2') ?? ''
    expect(saved).toContain('"positions"')
    expect(saved).not.toContain('"activity"')
  })

  it('remembers the layout across reloads, and Reset layout restores the default', async () => {
    const { unmount } = render(<App />)
    setPanel('Order Entry', 'Off')
    // The layout is saved shortly after it changes.
    await waitFor(() => {
      const saved = localStorage.getItem('paper-trade.layout.v2') ?? ''
      expect(saved).toContain('"watchlist"')
      expect(saved).not.toContain('"ticket"')
    })
    unmount()

    render(<App />)
    expect(hasPanel('Order Entry')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Reset layout/ }))
    expect(panel('Order Entry')).toBeInTheDocument()
    expect(stateOf('Order Entry')).toBe('Collapsed')
  })

  it('saves the layout under StrictMode too, as the real page runs', async () => {
    const { unmount } = render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
    setPanel('Activity', 'Off')
    await waitFor(() => expect(localStorage.getItem('paper-trade.layout.v2') ?? '').toContain('"positions"'))
    expect(localStorage.getItem('paper-trade.layout.v2')).not.toContain('"activity"')
    unmount()
  })

  it("market ticks and the workspace's own re-renders don't touch dockview's options (that re-laid the grid out mid-drag)", () => {
    const update = vi.spyOn(DockviewApi.prototype, 'updateOptions')
    render(<App />)
    push(tick())
    update.mockClear()
    push(tick({ regime: 'volatile' }))
    push(tick({ regime: 'calm' }))
    // The workspace's own re-renders (peeking) mustn't either.
    fireEvent.click(stripTab('Watchlist'))
    fireEvent.pointerDown(panel('Chart'))
    expect(update).not.toHaveBeenCalled()
  })

  it('Reset layout after floating panels builds the default as one row: the column and the chart', async () => {
    render(<App />)
    setPanel('Watchlist', 'Float')
    setPanel('Chart', 'Float')
    fireEvent.click(screen.getByRole('menuitem', { name: /Reset layout/ }))
    await waitFor(() => {
      const saved = JSON.parse(localStorage.getItem('paper-trade.layout.v2') ?? '{}')
      expect(saved.floatingGroups ?? []).toHaveLength(0)
      // Not wrapped in an extra branch (which came out 0px tall and showed nothing).
      expect(saved.grid.orientation).toBe('HORIZONTAL')
      expect(saved.grid.root.data.map((n: { type: string }) => n.type)).toEqual(['branch', 'leaf'])
    })
    expect(panel('Chart').closest('.dv-resize-container')).toBeNull()
  })

  it('Reset layout lays the rebuilt grid out at the workspace size', () => {
    const size = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1200)
    const height = vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(700)
    const layout = vi.spyOn(DockviewApi.prototype, 'layout')
    try {
      render(<App />)
      setPanel('Watchlist', 'Float')
      layout.mockClear()
      fireEvent.click(screen.getByRole('menuitem', { name: /Reset layout/ }))
      expect(layout).toHaveBeenCalledWith(1200, 700, true)
    } finally {
      size.mockRestore()
      height.mockRestore()
      layout.mockRestore()
    }
  })

  it('Reset layout drops a peek, so it cannot land on a new group with the same id', () => {
    render(<App />)
    fireEvent.click(stripTab('Watchlist'))
    fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Reset layout/ }))
    expect(document.querySelector('.peeking')).toBeNull()
    expect(stripTab('Watchlist')).toHaveAttribute('aria-pressed', 'false')
  })

  it('ignores a corrupted saved layout', () => {
    localStorage.setItem('paper-trade.layout.v2', '{"grid": "nonsense"}')
    render(<App />)
    expect(panel('Chart')).toBeInTheDocument()
    expect(stripTab('Order Entry')).toBeInTheDocument()
  })
})

describe('full screen', () => {
  it('the top bar button and F toggle full screen, but not while typing', () => {
    const request = vi.fn(() => Promise.resolve())
    Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true })
    Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: request })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Full screen' }))
    expect(request).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document.body, { key: 'f' })
    expect(request).toHaveBeenCalledTimes(2)
    const input = document.createElement('input')
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: 'f' })
    expect(request).toHaveBeenCalledTimes(2)
    input.remove()
    Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: false })
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
