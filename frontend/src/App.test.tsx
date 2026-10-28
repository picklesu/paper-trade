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
    realized: null,
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

const openMenu = () => {
  if (!screen.queryByRole('menu', { name: 'Panels' })) fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
}
const closeMenu = () => {
  if (screen.queryByRole('menu', { name: 'Panels' })) fireEvent.click(screen.getByRole('button', { name: 'Panels ▾' }))
}
/** Turn a panel on or off from the Panels menu. */
function setOn(name: string, on: boolean) {
  openMenu()
  const toggle = screen.getByRole('switch', { name: `${name} on` })
  if ((toggle.getAttribute('aria-checked') === 'true') !== on) fireEvent.click(toggle)
  closeMenu()
}
/** Move a panel to an edge from the Panels menu. */
function setEdge(name: string, edge: 'Left' | 'Right' | 'Top' | 'Bottom') {
  openMenu()
  fireEvent.click(within(screen.getByRole('radiogroup', { name: `${name} edge` })).getByRole('radio', { name: `${edge} edge` }))
  closeMenu()
}
/** The edge strip on one edge, and the tabs on it. */
const strip = (edge: string) => screen.queryByRole('toolbar', { name: `Panels (${edge})` })
const tabsOn = (edge: string) => (strip(edge) ? within(strip(edge)!).getAllByRole('button').map((b) => b.textContent) : [])
/** A panel's tab, on whichever edge it's on. */
const stripTab = (name: string | RegExp) => {
  for (const s of screen.queryAllByRole('toolbar', { name: /^Panels \(/ })) {
    const tab = within(s).queryByRole('button', { name })
    if (tab) return tab
  }
  throw new Error(`no tab ${name}`)
}
const isOpen = (name: string | RegExp) => stripTab(name).getAttribute('aria-pressed') === 'true'
const group = (name: string) => panel(name).closest('.dv-groupview') as HTMLElement

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('lays out the default workspace before data arrives: the chart, Watchlist and Order Entry collapsed left, Positions and Orders bottom', () => {
    render(<App />)
    expect(panel('Chart')).toBeInTheDocument()
    expect(tabsOn('left')).toEqual(['Watchlist', 'Order Entry'])
    expect(tabsOn('bottom')).toEqual(['Positions', 'Orders'])
    for (const name of ['Watchlist', 'Order Entry', 'Positions', 'Orders']) expect(isOpen(name)).toBe(false)
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
            order({ id: 4, status: 'working', type: 'limit', limit_price: 570 }),
            order({ id: 3, side: 'sell', qty: 4, status: 'filled', fill_price: 585.5, filled_at: '2026-01-05T15:20:00Z', realized: 21.88 }),
            order({ id: 2, side: 'sell', qty: 2, status: 'filled', fill_price: 575, filled_at: '2026-01-05T15:10:00Z', realized: -10.06 }),
            // A GTC buy placed first that filled last.
            order({ id: 1, tif: 'gtc', status: 'filled', fill_price: 580.03, filled_at: '2026-01-05T15:30:00Z' }),
          ],
        }),
      })
    })
    expect(stripTab('Orders, 1 working')).toBeInTheDocument()
    fireEvent.click(stripTab(/^Orders/))
    const orders = panel('Orders')
    expect(within(orders).getByText('Limit 570.00')).toBeInTheDocument()
    expect(within(orders).getByText('Filled at 580.03')).toBeInTheDocument()
    fireEvent.click(within(orders).getByRole('button', { name: 'Cancel order 4' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/orders/4', expect.objectContaining({ method: 'DELETE' }))

    // Filled sells show the P/L they realized; a buy has none of its own.
    const row = (id: number) => within(orders).getAllByRole('row').find((r) => r.querySelector('td')?.textContent === String(id))!
    expect(within(row(3)).getByText('+$21.88')).toHaveClass('up')
    expect(within(row(2)).getByText('-$10.06')).toHaveClass('down')
    expect(within(row(1)).getByText('—')).toBeInTheDocument()

    // The sells shown are added up.
    expect(within(orders).getByText('Realized P/L of the sells shown').closest('tr')).toHaveTextContent('+$11.82')

    // Filters narrow the list; Filled lists trades in the order they filled, latest first.
    fireEvent.click(within(orders).getByRole('button', { name: 'Filled (3)' }))
    expect(within(orders).queryByText('Limit 570.00')).not.toBeInTheDocument()
    const ids = () => within(orders).getAllByRole('row').slice(1, -1).map((r) => r.querySelector('td')?.textContent)
    expect(ids()).toEqual(['1', '3', '2'])
    fireEvent.click(within(orders).getByRole('button', { name: 'Working (1)' }))
    expect(within(orders).queryByText('Realized P/L of the sells shown')).not.toBeInTheDocument()
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

  it('a tab opens its panel, which stays open until its tab is clicked again', async () => {
    render(<App />)
    fireEvent.click(stripTab('Watchlist'))
    await waitFor(() => expect(isOpen('Watchlist')).toBe(true))
    // Clicks elsewhere leave it open.
    fireEvent.pointerDown(panel('Chart'))
    fireEvent.click(panel('Chart'))
    expect(isOpen('Watchlist')).toBe(true)
    fireEvent.click(stripTab('Watchlist'))
    await waitFor(() => expect(isOpen('Watchlist')).toBe(false))
  })

  it('panels open on the same edge stack in it, in their usual order', async () => {
    render(<App />)
    fireEvent.click(stripTab('Order Entry'))
    fireEvent.click(stripTab('Watchlist'))
    await waitFor(() => expect(isOpen('Order Entry') && isOpen('Watchlist')).toBe(true))
    // Watchlist above Order Entry, both left of the chart.
    expect(group('Watchlist').compareDocumentPosition(group('Order Entry')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(group('Order Entry').compareDocumentPosition(group('Chart')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('the Panels menu moves a panel to any edge, keeping it open or collapsed', async () => {
    render(<App />)
    openMenu()
    // The chart always sits in the middle: it isn't in the menu.
    expect(screen.getAllByRole('switch').map((s) => s.getAttribute('aria-label'))).toEqual(['Watchlist on', 'Order Entry on', 'Positions on', 'Orders on'])
    closeMenu()

    fireEvent.click(stripTab('Order Entry'))
    setEdge('Order Entry', 'Right')
    await waitFor(() => expect(tabsOn('right')).toEqual(['Order Entry']))
    expect(isOpen('Order Entry')).toBe(true)
    expect(tabsOn('left')).toEqual(['Watchlist'])
    // Right of the chart.
    expect(group('Chart').compareDocumentPosition(group('Order Entry')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    setEdge('Watchlist', 'Top')
    await waitFor(() => expect(tabsOn('top')).toEqual(['Watchlist']))
    expect(isOpen('Watchlist')).toBe(false)
    expect(strip('left')).toBeNull()

    setEdge('Positions', 'Left')
    await waitFor(() => expect(tabsOn('left')).toEqual(['Positions']))
    expect(tabsOn('bottom')).toEqual(['Orders'])
  })

  it('Off removes a panel and its tab; On brings it back open on the edge it was on', async () => {
    render(<App />)
    setEdge('Orders', 'Right')
    await waitFor(() => expect(tabsOn('right')).toEqual(['Orders']))
    setOn('Orders', false)
    await waitFor(() => expect(strip('right')).toBeNull())
    expect(hasPanel('Orders')).toBe(false)
    // The menu still shows where it will come back; choosing another edge only changes that.
    openMenu()
    expect(within(screen.getByRole('radiogroup', { name: 'Orders edge' })).getByRole('radio', { checked: true })).toHaveAccessibleName('Right edge')
    closeMenu()
    setEdge('Orders', 'Top')
    expect(hasPanel('Orders')).toBe(false)
    setOn('Orders', true)
    await waitFor(() => expect(tabsOn('top')).toEqual(['Orders']))
    expect(isOpen('Orders')).toBe(true)
  })

  it('moving an open panel to another edge keeps what was typed into it', async () => {
    render(<App />)
    push(tick())
    fireEvent.click(stripTab('Order Entry'))
    await waitFor(() => expect(isOpen('Order Entry')).toBe(true))
    const qty = within(panel('Order Entry')).getByLabelText(/Quantity/)
    fireEvent.change(qty, { target: { value: '250' } })
    setEdge('Order Entry', 'Right')
    await waitFor(() => expect(tabsOn('right')).toEqual(['Order Entry']))
    expect(within(panel('Order Entry')).getByLabelText(/Quantity/)).toHaveValue(250)
  })

  it('saves the layout immediately when the page is closing', () => {
    render(<App />)
    localStorage.clear()
    setOn('Orders', false)
    window.dispatchEvent(new Event('pagehide')) // before the delayed save
    const saved = localStorage.getItem('paper-trade.layout.v3') ?? ''
    expect(saved).toContain('"positions"')
    expect(saved).not.toContain('"orders"')
  })

  it('remembers the layout across reloads, and Reset layout restores the default', async () => {
    const { unmount } = render(<App />)
    setEdge('Watchlist', 'Right')
    fireEvent.click(stripTab('Positions'))
    await waitFor(() => expect(isOpen('Positions')).toBe(true))
    window.dispatchEvent(new Event('pagehide'))
    unmount()

    render(<App />)
    expect(tabsOn('right')).toEqual(['Watchlist'])
    expect(isOpen('Positions')).toBe(true)
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: /Reset layout/ }))
    await waitFor(() => expect(tabsOn('left')).toEqual(['Watchlist', 'Order Entry']))
    expect(tabsOn('bottom')).toEqual(['Positions', 'Orders'])
    expect(isOpen('Positions')).toBe(false)
  })

  it('saves the layout under StrictMode too, as the real page runs', async () => {
    const { unmount } = render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
    setOn('Orders', false)
    await waitFor(() => {
      const saved = localStorage.getItem('paper-trade.layout.v3') ?? ''
      expect(saved).toContain('"positions"')
      expect(saved).not.toContain('"orders"')
    })
    unmount()
  })

  it("market ticks and the workspace's own re-renders don't touch dockview's options (that re-laid the grid out mid-drag)", async () => {
    const update = vi.spyOn(DockviewApi.prototype, 'updateOptions')
    render(<App />)
    push(tick())
    update.mockClear()
    push(tick({ regime: 'volatile' }))
    push(tick({ regime: 'calm' }))
    // The workspace's own re-renders (a panel opened and its tab updated) mustn't either.
    fireEvent.click(stripTab('Watchlist'))
    await waitFor(() => expect(isOpen('Watchlist')).toBe(true))
    expect(update).not.toHaveBeenCalled()
  })

  it('Reset layout lays the rebuilt grid out at the workspace size', () => {
    const size = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1200)
    const height = vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(700)
    const layout = vi.spyOn(DockviewApi.prototype, 'layout')
    try {
      render(<App />)
      setEdge('Watchlist', 'Right')
      layout.mockClear()
      openMenu()
      fireEvent.click(screen.getByRole('menuitem', { name: /Reset layout/ }))
      expect(layout).toHaveBeenCalledWith(1200, 700, true)
    } finally {
      size.mockRestore()
      height.mockRestore()
      layout.mockRestore()
    }
  })

  it('a saved layout with a panel that no longer exists loads without it, keeping the rest', async () => {
    const { unmount } = render(<App />)
    fireEvent.click(stripTab('Watchlist'))
    await waitFor(() => expect(isOpen('Watchlist')).toBe(true))
    window.dispatchEvent(new Event('pagehide'))
    unmount()
    // Add an Activity panel (removed in #21) to the saved layout.
    const saved = JSON.parse(localStorage.getItem('paper-trade.layout.v3')!)
    saved.grid.root.data.push({ type: 'leaf', data: { views: ['activity'], activeView: 'activity', id: '99', hideHeader: true }, size: 100, visible: false })
    saved.panels.activity = { id: 'activity', contentComponent: 'activity', title: 'Activity' }
    saved.activeGroup = '99'
    localStorage.setItem('paper-trade.layout.v3', JSON.stringify(saved))

    render(<App />)
    expect(isOpen('Watchlist')).toBe(true) // the saved layout, not the default
    expect(hasPanel('Activity')).toBe(false)
  })

  it('ignores a corrupted saved layout', () => {
    localStorage.setItem('paper-trade.layout.v3', '{"grid": "nonsense"}')
    render(<App />)
    expect(panel('Chart')).toBeInTheDocument()
    expect(tabsOn('left')).toEqual(['Watchlist', 'Order Entry'])
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
