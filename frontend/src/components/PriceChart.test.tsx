import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Bar } from '../lib/bars'
import { calls, resetCalls } from '../test/lightweight-charts.mock'
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
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: '5Y' }))
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2))
    expect(param(fn, 1, 'interval')).toBe('1W')
    fireEvent.click(screen.getByRole('button', { name: '15-minute bars' }))
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(3))
    expect(param(fn, 2, 'interval')).toBe('15m')
    expect(screen.getByRole('button', { name: '15-minute bars' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '5Y' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('reloads when the timeline changes (a jump or rewind)', async () => {
    const fn = serve()
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1))
    rerender(<PriceChart quote={quote} clock={{ ...clock, timeline: 1 }} orders={[]} />)
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(2))
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
