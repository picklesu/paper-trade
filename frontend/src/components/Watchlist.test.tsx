import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mergeQuotes } from '../lib/stream'
import type { ClockState, Quote, Watchlist as List } from '../lib/types'
import { Watchlist } from './Watchlist'

function quote(symbol: string, last: number, prev_close: number): Quote {
  const change = Math.round((last - prev_close) * 100) / 100
  return {
    symbol,
    name: `${symbol} Inc`,
    last,
    bid: last - 0.01,
    ask: last + 0.02,
    open: prev_close,
    high: Math.max(last, prev_close) + 1,
    low: Math.min(last, prev_close) - 1,
    prev_close,
    change,
    change_pct: Math.round((change / prev_close) * 10000) / 100,
    volume: 1_500_000,
    bid_size: 300,
    ask_size: 1200,
  }
}

const clock: ClockState = {
  time: '2026-01-05T15:30:00Z',
  speed: 1,
  paused: false,
  speeds: [1],
  origin: '2025-01-05T14:30:00Z',
  session_open: '2026-01-05T14:30:00Z',
  timeline: 0,
  building_history: false,
}
const SYMBOLS = ['SPY', 'AAPL', 'MSFT', 'XOM'].map((s) => ({ symbol: s, name: `${s} Inc`, sector: null }))
const quotes = mergeQuotes(new Map(), [quote('SPY', 582, 580), quote('AAPL', 228, 230), quote('MSFT', 410, 400)])
const main: List = { id: 1, name: 'Main', symbols: ['SPY', 'AAPL', 'MSFT'] }
const other: List = { id: 2, name: 'Energy', symbols: ['XOM'] }

/** Answers the watchlist endpoints like the server, recording what was asked. */
function mockServer() {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {}
    if (url.startsWith('/api/sparklines')) return { ok: true, json: async () => ({ step_minutes: 5, session_open: clock.session_open, series: { SPY: [580, 581, 582] } }) }
    if (url === '/api/watchlists' && init?.method === 'POST') return { ok: true, json: async () => ({ id: 3, name: body.name, symbols: [] }) }
    if (init?.method === 'PATCH' && body.symbols?.includes('NOPE'))
      return { ok: false, status: 422, json: async () => ({ detail: 'unknown symbol: NOPE' }) }
    return { ok: true, json: async () => ({ id: 1, name: 'Main', symbols: body.symbols ?? main.symbols, ...body }) }
  })
  vi.stubGlobal('fetch', fn)
  return fn
}
const calls = (fn: ReturnType<typeof mockServer>, method: string) =>
  fn.mock.calls.filter(([, init]) => init?.method === method).map(([url, init]) => [url, JSON.parse(String(init!.body ?? '{}'))])

function renderList(overrides: Partial<Parameters<typeof Watchlist>[0]> = {}) {
  const props = {
    lists: [main, other],
    list: main,
    onPickList: vi.fn(),
    quotes,
    stats: { SPY: { high_52w: 600, low_52w: 500, avg_volume: 2_000_000 } },
    held: new Map([['AAPL', 10]]),
    symbols: SYMBOLS,
    clock,
    selected: 'SPY',
    onSelect: vi.fn(),
    ...overrides,
  }
  return { ...render(<Watchlist {...props} />), props }
}
const rowSymbols = () => screen.getAllByRole('row').slice(1).map((r) => within(r).queryAllByRole('cell')[0]?.textContent).filter(Boolean)

describe('Watchlist', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    mockServer()
  })

  it('shows the list in its order, with a held marker and the default columns', () => {
    renderList()
    expect(rowSymbols()).toEqual(['SPY', 'AAPL', 'MSFT'])
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Symbol', 'Last', 'Chg %', 'Today', ''])
    expect(screen.getByLabelText('held: 10 shares')).toBeInTheDocument()
    expect(screen.getByText('-0.87%')).toHaveClass('down')
    fireEvent.click(screen.getByText('MSFT'))
  })

  it('switches lists from the picker', () => {
    const { props } = renderList()
    fireEvent.change(screen.getByLabelText('Watchlist'), { target: { value: '2' } })
    expect(props.onPickList).toHaveBeenCalledWith(2)
  })

  it('sorts by a column: high to low, low to high, then the list order (saved)', () => {
    renderList()
    const header = () => screen.getByRole('button', { name: /^Chg %/ })
    fireEvent.click(header())
    expect(rowSymbols()).toEqual(['MSFT', 'SPY', 'AAPL'])
    fireEvent.click(header())
    expect(rowSymbols()).toEqual(['AAPL', 'SPY', 'MSFT'])
    expect(JSON.parse(localStorage.getItem('paper-trade.watchlist.sort')!)).toEqual({ key: 'change_pct', dir: 1 })
    fireEvent.click(header())
    expect(rowSymbols()).toEqual(['SPY', 'AAPL', 'MSFT'])
  })

  it('chooses columns from the menu (saved)', () => {
    renderList()
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist menu' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Bid size/ }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /52-week high/ }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Today’s chart/ }))
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Symbol', 'Last', 'Chg %', 'Bid Sz', '52W High', ''])
    expect(screen.getAllByText('300')).toHaveLength(3)
    expect(screen.getByText('600.00')).toBeInTheDocument() // SPY: the stored 52-week high
    expect(JSON.parse(localStorage.getItem('paper-trade.watchlist.columns')!)).toEqual(['last', 'change_pct', 'bid_size', 'high_52w'])
  })

  it('adds a symbol from the search and removes one', async () => {
    const fn = mockServer()
    const { props } = renderList()
    fireEvent.click(screen.getByRole('button', { name: 'Add symbol' }))
    const field = screen.getByRole('combobox', { name: 'Add a symbol to Main' })
    fireEvent.change(field, { target: { value: 'xo' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(rowSymbols()).toEqual(['SPY', 'AAPL', 'MSFT', 'XOM']) // shown before the server answers
    expect(props.onSelect).toHaveBeenCalledWith('XOM')
    await waitFor(() => expect(calls(fn, 'PATCH')).toEqual([['/api/watchlists/1', { symbols: ['SPY', 'AAPL', 'MSFT', 'XOM'] }]]))

    fireEvent.click(screen.getByRole('button', { name: 'Remove AAPL' }))
    await waitFor(() => expect(calls(fn, 'PATCH')[1][1]).toEqual({ symbols: ['SPY', 'MSFT', 'XOM'] }))
    expect(props.onSelect).not.toHaveBeenCalledWith('AAPL') // removing doesn't select
  })

  it('sends quick edits one after another, in order', async () => {
    const order: string[] = []
    let release: () => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === 'PATCH') {
          const symbols = JSON.parse(String(init.body)).symbols
          order.push(symbols.join(','))
          if (order.length === 1) await new Promise<void>((r) => (release = r)) // the first is slow
          return { ok: true, json: async () => ({ id: 1, name: 'Main', symbols }) }
        }
        return { ok: true, json: async () => ({ step_minutes: 5, session_open: '', series: {} }) }
      }),
    )
    renderList()
    fireEvent.click(screen.getByRole('button', { name: 'Remove AAPL' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove MSFT' }))
    await waitFor(() => expect(order).toEqual(['SPY,MSFT']))
    release()
    await waitFor(() => expect(order).toEqual(['SPY,MSFT', 'SPY']))
  })

  it('reorders by dragging a row', async () => {
    const fn = mockServer()
    renderList()
    const row = (s: string) => screen.getByText(s).closest('tr')!
    fireEvent.dragStart(row('MSFT'), { dataTransfer: { setData() {}, effectAllowed: '' } })
    fireEvent.dragOver(row('SPY'))
    fireEvent.drop(row('SPY'))
    expect(rowSymbols()).toEqual(['MSFT', 'SPY', 'AAPL'])
    await waitFor(() => expect(calls(fn, 'PATCH')).toEqual([['/api/watchlists/1', { symbols: ['MSFT', 'SPY', 'AAPL'] }]]))
  })

  it('shows the server’s reason when a change is refused, and goes back to the saved order', async () => {
    renderList({ symbols: [...SYMBOLS, { symbol: 'NOPE', name: 'Nope', sector: null }] })
    fireEvent.click(screen.getByRole('button', { name: 'Add symbol' }))
    const field = screen.getByRole('combobox', { name: 'Add a symbol to Main' })
    fireEvent.change(field, { target: { value: 'NOPE' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(await screen.findByRole('alert')).toHaveTextContent('unknown symbol: NOPE')
    expect(rowSymbols()).toEqual(['SPY', 'AAPL', 'MSFT'])
  })

  it('creates, renames and deletes lists', async () => {
    const fn = mockServer()
    const { props } = renderList()
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist menu' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'New list…' }))
    const name = screen.getByRole('textbox', { name: 'New watchlist name' })
    fireEvent.change(name, { target: { value: 'Tech' } })
    fireEvent.keyDown(name, { key: 'Enter' })
    await waitFor(() => expect(props.onPickList).toHaveBeenCalledWith(3))
    expect(calls(fn, 'POST')).toEqual([['/api/watchlists', { name: 'Tech', symbols: [] }]])

    fireEvent.click(screen.getByRole('button', { name: 'Watchlist menu' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename “Main”…' }))
    const rename = screen.getByRole('textbox', { name: 'Rename Main' })
    fireEvent.change(rename, { target: { value: 'Core' } })
    fireEvent.keyDown(rename, { key: 'Enter' })
    await waitFor(() => expect(calls(fn, 'PATCH')).toEqual([['/api/watchlists/1', { name: 'Core' }]]))

    fireEvent.click(screen.getByRole('button', { name: 'Watchlist menu' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete “Main”…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(fn.mock.calls.some(([url, init]) => url === '/api/watchlists/1' && init?.method === 'DELETE')).toBe(true))
  })

  it('draws today’s sparkline from the server with the live price at the end', async () => {
    const { container } = renderList()
    await waitFor(() => expect(container.querySelector('.sparkline.up polyline')).not.toBeNull())
    const points = container.querySelector('.sparkline.up polyline')!.getAttribute('points')!.split(' ')
    expect(points).toHaveLength(3)
  })

  it('says when a list is empty', () => {
    renderList({ list: { id: 4, name: 'Empty', symbols: [] } })
    expect(screen.getByText(/No symbols in Empty yet/)).toBeInTheDocument()
  })
})

describe('mergeQuotes', () => {
  it('keeps unchanged quotes as the same object, so rows can skip re-rendering', () => {
    const a = quote('SPY', 582, 580)
    const b = quote('AAPL', 228, 230)
    const first = mergeQuotes(new Map(), [a, b])
    const second = mergeQuotes(first, [{ ...a }, quote('AAPL', 229, 230)])
    expect(second.get('SPY')).toBe(a)
    expect(second.get('AAPL')!.last).toBe(229)
    expect(mergeQuotes(second, []).get('SPY')).toBe(a) // unsubscribed symbols keep their last quote
    expect(mergeQuotes(second, [b], true).has('SPY')).toBe(false) // but not across a time jump
  })
})

describe('Watchlist flash', () => {
  it('flashes a changed price', () => {
    vi.useFakeTimers()
    const { rerender, props } = renderList()
    const next = mergeQuotes(quotes, [quote('SPY', 583, 580)])
    rerender(<Watchlist {...props} quotes={next} />)
    expect(screen.getByText('583.00')).toHaveClass('flash-up')
    act(() => void vi.advanceTimersByTime(500))
    expect(screen.getByText('583.00')).not.toHaveClass('flash-up')
    vi.useRealTimers()
  })
})
