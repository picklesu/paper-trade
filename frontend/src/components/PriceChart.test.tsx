import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Bar } from '../lib/bars'
import { calls, createChart, resetCalls } from '../test/lightweight-charts.mock'
import type { ClockState, Order, Quote } from '../lib/types'
import { PriceChart } from './PriceChart'

const OPEN = Date.parse('2026-01-07T14:30:00Z') / 1000
const clock: ClockState = {
  time: '2026-01-07T15:00:00Z',
  speed: 1,
  paused: true,
  speeds: [1, 10, 60, 600],
  origin: '2021-01-04T14:30:00Z',
  session_open: '2026-01-07T14:30:00Z',
  timeline: 0,
  building_history: false,
}
const quote = { symbol: 'SPY', last: 581.4 } as Quote
const bars: Bar[] = [
  [OPEN, 580, 581, 579.5, 580.5, 1000],
  [OPEN + 300, 580.5, 582, 580, 581.2, 2000],
]

function serve(page = { bars, more: true }) {
  const fn = vi.fn(async (_url: string) => ({ ok: true, status: 200, json: async () => page }))
  vi.stubGlobal('fetch', fn)
  return fn
}

const param = (fn: ReturnType<typeof serve>, i: number, key: string) => new URL(fn.mock.calls[i][0], 'http://x').searchParams.get(key)

describe('PriceChart', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    resetCalls()
  })

  it('loads 5-minute bars for the default 5-day range and shows the latest bar', async () => {
    const fn = serve()
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(calls.candleData).toHaveLength(2))
    expect(param(fn, 0, 'symbol')).toBe('SPY')
    expect(param(fn, 0, 'interval')).toBe('5m')
    expect(calls.volumeData).toHaveLength(2)
    expect(calls.visibleRange).toMatchObject({ from: OPEN + 300 - 7 * 86400, to: OPEN + 300 })
    expect(screen.getByLabelText('Bar details')).toHaveTextContent('C 581.20')
  })

  it('range presets pick a bar size; bar sizes can be chosen directly', async () => {
    const fn = serve()
    // Full loads only (older pages, which may follow, carry an `end`).
    const loads = () => fn.mock.calls.map(([u]) => new URL(u, 'http://x').searchParams).filter((p) => !p.has('end'))
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(loads()).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: '5Y' }))
    await waitFor(() => expect(loads()).toHaveLength(2))
    expect(loads()[1].get('interval')).toBe('1W')
    fireEvent.click(screen.getByRole('button', { name: '15-minute bars' }))
    await waitFor(() => expect(loads()).toHaveLength(3))
    expect(loads()[2].get('interval')).toBe('15m')
    expect(screen.getByRole('button', { name: '15-minute bars' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '5Y' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('puts range and bar size in drop-downs when the chart is narrow', async () => {
    const fn = serve()
    const loads = () => fn.mock.calls.map(([u]) => new URL(u, 'http://x').searchParams).filter((p) => !p.has('end'))
    // A ResizeObserver that reports a 400px-wide chart, with buttons reaching 500px.
    const RO = globalThis.ResizeObserver
    const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      return { left: 0, right: this.classList.contains('seg') ? 500 : 400, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {} } as DOMRect
    })
    globalThis.ResizeObserver = class {
      cb: ResizeObserverCallback
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb
      }
      observe(el: Element) {
        this.cb([{ contentRect: { width: 400, height: 300 }, target: el } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver)
      }
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
    try {
      render(<PriceChart quote={quote} clock={clock} orders={[]} />)
      await waitFor(() => expect(loads()).toHaveLength(1))
      expect(screen.queryByRole('button', { name: '5Y' })).not.toBeInTheDocument()
      fireEvent.change(screen.getByRole('combobox', { name: 'Range' }), { target: { value: '5Y' } })
      await waitFor(() => expect(loads()).toHaveLength(2))
      expect(loads()[1].get('interval')).toBe('1W')
      expect(screen.getByRole('combobox', { name: 'Bar size' })).toHaveValue('1W')
      fireEvent.change(screen.getByRole('combobox', { name: 'Bar size' }), { target: { value: '15m' } })
      await waitFor(() => expect(loads()).toHaveLength(3))
      expect(loads()[2].get('interval')).toBe('15m')
    } finally {
      globalThis.ResizeObserver = RO
      rect.mockRestore()
    }
  })

  it('has no date axis: the legend shows the bar time', async () => {
    serve()
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    const options = (createChart.mock.calls.at(-1) as unknown as [unknown, { timeScale: { visible: boolean } }])[1]
    expect(options.timeScale.visible).toBe(false)
  })

  it('reloads when the timeline changes (a jump or rewind)', async () => {
    const fn = serve()
    const loads = () => fn.mock.calls.filter(([u]) => !String(u).includes('end=')).length
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(loads()).toBe(1))
    rerender(<PriceChart quote={quote} clock={{ ...clock, timeline: 1 }} orders={[]} />)
    await waitFor(() => expect(loads()).toBe(2))
  })

  it('marks your fills on the chart', async () => {
    serve()
    const fill = {
      id: 1,
      symbol: 'SPY',
      side: 'buy',
      qty: 10,
      status: 'filled',
      filled_at: new Date((OPEN + 400) * 1000).toISOString(),
    } as Order
    render(<PriceChart quote={quote} clock={clock} orders={[fill]} />)
    await waitFor(() => expect(calls.markers).toHaveLength(1))
    expect(calls.markers[0]).toMatchObject({ time: OPEN + 300, shape: 'arrowUp', text: 'B 10' })
  })

  it('folds live ticks into the last bar', async () => {
    serve()
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(calls.candleData).toHaveLength(2))
    act(() => {
      rerender(<PriceChart quote={{ ...quote, last: 583.5 }} clock={{ ...clock, time: '2026-01-07T14:36:00Z' }} orders={[]} />)
    })
    expect(calls.updates.at(-1)).toMatchObject({ time: OPEN + 300, high: 583.5, close: 583.5 })
  })

  it('says when history is still being built, and when loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) }))
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    expect(await screen.findByText(/Couldn't load the chart: boom/)).toBeInTheDocument()
    serve()
    rerender(
      <PriceChart quote={quote} clock={{ ...clock, timeline: 2, building_history: true }} orders={[]} history={{ done: 630, total: 1260 }} />,
    )
    expect(await screen.findByText('Building 5 years of price history… 50%')).toBeInTheDocument()
  })
})

describe('PriceChart history paging', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    resetCalls()
  })

  it('keeps the same bars in view when older bars are added on the left', async () => {
    const older: Bar[] = [[OPEN - 600, 1, 1, 1, 1, 1], [OPEN - 300, 1, 1, 1, 1, 1]]
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ({
        ok: true,
        status: 200,
        json: async () => (url.includes('end=') ? { bars: older, more: false } : { bars, more: true }),
      })),
    )
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(calls.candleData).toHaveLength(2))
    calls.logicalRange = { from: 30, to: 35 } // settled view: not near the start yet
    await new Promise((r) => setTimeout(r, 120))
    calls.logicalRange = { from: 0.5, to: 5.5 } // the user scrolls back, with a right margin past the last bar
    act(() => calls.onRangeChange!({ from: 0.5, to: 5.5 }))
    await waitFor(() => expect(calls.candleData).toHaveLength(4))
    // Shifted by the 2 bars added: same dates, same zoom, same margin.
    expect(calls.logicalRange).toEqual({ from: 2.5, to: 7.5 })
  })
})

describe('PriceChart settling', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    resetCalls()
  })

  it("doesn't load older bars while a fresh view is still being applied, then loads if needed", async () => {
    const fn = vi.fn(async (_url: string) => ({ ok: true, status: 200, json: async () => ({ bars, more: true }) }))
    vi.stubGlobal('fetch', fn)
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(calls.candleData).toHaveLength(2))
    act(() => calls.onRangeChange!({ from: 0, to: 2 })) // a range event fired during the load
    const older = () => fn.mock.calls.filter(([u]) => String(u).includes('end=')).length
    expect(older()).toBe(0)
    // After settling, the applied view starts at the first bar: load older bars once.
    calls.logicalRange = { from: 0, to: 2 }
    await waitFor(() => expect(older()).toBe(1))
  })
})

describe('PriceChart races', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    resetCalls()
  })

  it('ignores an older page that arrives after switching symbols', async () => {
    let resolveOlder: (v: unknown) => void = () => {}
    const fn = vi.fn((url: string) => {
      const u = new URL(url, 'http://x')
      if (u.searchParams.get('symbol') === 'SPY' && u.searchParams.has('end')) {
        return new Promise((r) => (resolveOlder = r)) // SPY's older page answers late
      }
      const page = u.searchParams.get('symbol') === 'SPY' ? { bars, more: true } : { bars: [[OPEN, 1, 1, 1, 1, 1]], more: false }
      return Promise.resolve({ ok: true, status: 200, json: async () => page })
    })
    vi.stubGlobal('fetch', fn)
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(calls.candleData).toHaveLength(2))
    await new Promise((r) => setTimeout(r, 120)) // the fresh view settles
    act(() => calls.onRangeChange!({ from: 0, to: 50 })) // scrolled back: load older SPY bars
    await waitFor(() => expect(fn.mock.calls.some(([u]) => String(u).includes('end='))).toBe(true))

    rerender(<PriceChart quote={{ ...quote, symbol: 'AAPL' }} clock={clock} orders={[]} />)
    await waitFor(() => expect(calls.candleData).toHaveLength(1))
    resolveOlder({ ok: true, status: 200, json: async () => ({ bars: [[OPEN - 300, 2, 2, 2, 2, 2]], more: true }) })
    await new Promise((r) => setTimeout(r, 20))
    expect(calls.candleData).toHaveLength(1) // still only AAPL's bar, no SPY bars mixed in
  })

  it('does not fold ticks into bars of a different bar size while reloading', async () => {
    let hold: (v: unknown) => void = () => {}
    let first = true
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        if (first) {
          first = false
          return Promise.resolve({ ok: true, status: 200, json: async () => ({ bars, more: true }) })
        }
        return new Promise((r) => (hold = r)) // the 1m reload is slow
      }),
    )
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(calls.candleData).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: '1-minute bars' }))
    const updatesBefore = calls.updates.length
    act(() => {
      rerender(<PriceChart quote={{ ...quote, last: 590 }} clock={{ ...clock, time: '2026-01-07T15:03:00Z' }} orders={[]} />)
    })
    expect(calls.updates.length).toBe(updatesBefore) // no 1m bar glued onto the 5m series
    hold({ ok: true, status: 200, json: async () => ({ bars, more: true }) })
  })
})
