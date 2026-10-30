import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Bar } from '../lib/bars'
import type { ClockState, Quote } from '../lib/types'
import { calls, resetCalls } from '../test/lightweight-charts.mock'
import { PriceChart } from './PriceChart'

const OPEN = Date.parse('2026-01-07T14:30:00Z') / 1000
const clock: ClockState = {
  time: '2026-01-07T20:59:00Z',
  speed: 1,
  paused: true,
  speeds: [1, 10, 60, 600],
  origin: '2021-01-04T14:30:00Z',
  session_open: '2026-01-07T14:30:00Z',
  timeline: 0,
  building_history: false,
}
const quote = { symbol: 'SPY', last: 600 } as Quote
// 78 five-minute bars of a steady climb: one session.
const bars: Bar[] = Array.from({ length: 78 }, (_, i) => [OPEN + i * 300, 500 + i, 501 + i, 499 + i, 500 + i, 1000])

function serve() {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ bars, more: false }) })))
}

const lines = () => calls.indicators.filter((s) => s.type === 'Line')
const lastValue = (s: { data: unknown[] }) => (s.data.at(-1) as { value?: number }).value

describe('chart indicators', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    resetCalls()
    localStorage.clear()
  })

  it('draws SMA 20 and SMA 50 by default, with correct values', async () => {
    serve()
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()[0]?.data).toHaveLength(78))
    expect(lines()).toHaveLength(2)
    // A straight-line climb: the SMA of the last n closes is the middle close.
    expect(lastValue(lines()[0])).toBeCloseTo(577 - 9.5) // SMA 20 of closes 558..577
    expect(lastValue(lines()[1])).toBeCloseTo(577 - 24.5) // SMA 50
    expect(lines()[0].data[18]).toEqual({ time: OPEN + 18 * 300 }) // not enough history yet: blank
    expect(screen.getByLabelText('Bar details')).toHaveTextContent('SMA 20 567.50')
  })

  it('turning on RSI and MACD adds panes; turning them off removes them', async () => {
    serve()
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: /Indicators/ }))
    const panel = screen.getByRole('group', { name: 'Indicators' })
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'RSI 14' }))
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'MACD 12 26 9' }))

    expect(calls.panes).toBe(3)
    const rsi = calls.indicators.find((s) => s.paneIndex === 1 && s.type === 'Line')!
    expect(rsi.priceLines).toHaveLength(2) // 70 and 30
    expect(lastValue(rsi)).toBe(100) // only gains
    const macdPane = calls.indicators.filter((s) => s.paneIndex === 2)
    expect(macdPane.map((s) => s.type).sort()).toEqual(['Histogram', 'Line', 'Line'])

    // Their values sit at the top of their own panes (100px apart in the mock), not with the price's.
    expect(screen.getByLabelText('Bar details')).not.toHaveTextContent('RSI')
    const rsiLegend = await screen.findByLabelText('RSI 14 values')
    expect(rsiLegend).toHaveTextContent('RSI 14 100.00')
    expect(rsiLegend.style.top).toBe('104px')
    expect(screen.getByLabelText('MACD 12 26 9 values').style.top).toBe('204px')

    fireEvent.click(within(panel).getByRole('checkbox', { name: 'RSI 14' }))
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'MACD 12 26 9' }))
    expect(calls.panes).toBe(1)
    expect(lines()).toHaveLength(2)
  })

  it('remembers choices and lengths', async () => {
    serve()
    const { unmount } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: /Indicators/ }))
    const input = within(screen.getByRole('group', { name: 'Indicators' })).getAllByRole('spinbutton')[0]
    fireEvent.change(input, { target: { value: '10' } })
    fireEvent.blur(input)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Bollinger Bands 20 2' }))
    unmount()

    resetCalls()
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()).toHaveLength(5)) // SMA 10, SMA 50, BB x3
    expect(lastValue(lines()[0])).toBeCloseTo(577 - 4.5) // SMA 10
  })

  it('VWAP is only offered for intraday bars', async () => {
    serve()
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: /Indicators/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'VWAP' }))
    expect(lines()).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: 'Daily bars' }))
    await waitFor(() => expect(lines()).toHaveLength(2))
    expect(screen.getByText('intraday bars only')).toBeInTheDocument()
  })

  it('live ticks update the last indicator values', async () => {
    serve()
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()[0]?.data).toHaveLength(78))
    act(() => {
      rerender(<PriceChart quote={{ ...quote, last: 677 }} clock={clock} orders={[]} />)
    })
    // The last close jumps from 577 to 677: SMA 20 rises by 100/20.
    expect((lines()[0].updates.at(-1) as { value: number }).value).toBeCloseTo(567.5 + 5)
  })
})

describe('chart indicators: review fixes', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    resetCalls()
    localStorage.clear()
  })

  it('the legend follows live ticks within the same bar', async () => {
    serve()
    const { rerender } = render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Bar details')).toHaveTextContent('SMA 20 567.50'))
    act(() => {
      rerender(<PriceChart quote={{ ...quote, last: 677 }} clock={clock} orders={[]} />)
    })
    expect(screen.getByLabelText('Bar details')).toHaveTextContent('C 677.00')
    expect(screen.getByLabelText('Bar details')).toHaveTextContent('SMA 20 572.50')
  })

  it("doesn't draw indicators from the old bar size while the new one loads", async () => {
    let release: (v: unknown) => void = () => {}
    let n = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        n++ === 0
          ? Promise.resolve({ ok: true, status: 200, json: async () => ({ bars, more: false }) })
          : new Promise((r) => (release = r)),
      ),
    )
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()[0]?.data).toHaveLength(78))
    fireEvent.click(screen.getByRole('button', { name: 'Daily bars' }))
    // New series exist, but stay empty until the daily bars arrive.
    expect(lines()).toHaveLength(2)
    expect(lines().every((s) => s.data.length === 0)).toBe(true)
    release({ ok: true, status: 200, json: async () => ({ bars: bars.slice(0, 30), more: false }) })
    await waitFor(() => expect(lines()[0].data).toHaveLength(30))
  })

  it('the menu counts only indicators drawn for this bar size', async () => {
    serve()
    render(<PriceChart quote={quote} clock={clock} orders={[]} />)
    await waitFor(() => expect(lines()).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: /Indicators/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'VWAP' }))
    expect(screen.getByRole('button', { name: /Indicators/ })).toHaveTextContent('Indicators (3)')
    fireEvent.click(screen.getByRole('button', { name: 'Daily bars' }))
    expect(screen.getByRole('button', { name: /Indicators/ })).toHaveTextContent('Indicators (2)')
  })
})
