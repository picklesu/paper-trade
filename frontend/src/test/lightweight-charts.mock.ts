// Test stand-in for lightweight-charts (it draws on <canvas>, which jsdom lacks).
// Records what the component asks of it so tests can check data, markers, panes and ranges.
import { vi } from 'vitest'

export interface MockSeries {
  kind: 'candle' | 'volume' | 'indicator'
  type: string
  options: Record<string, unknown>
  paneIndex: number
  data: unknown[]
  updates: unknown[]
  priceLines: unknown[]
}

export const calls = {
  candleData: [] as unknown[],
  volumeData: [] as unknown[],
  markers: [] as unknown[],
  visibleRange: null as unknown,
  updates: [] as unknown[],
  /** The chart's visible-range listener, so tests can simulate scrolling back. */
  onRangeChange: null as ((range: { from: number; to: number }) => void) | null,
  /** Indicator series currently on the chart. */
  indicators: [] as MockSeries[],
  /** Pane count (the price pane plus indicator panes). */
  panes: 1,
}

export function resetCalls() {
  calls.candleData = []
  calls.volumeData = []
  calls.markers = []
  calls.visibleRange = null
  calls.updates = []
  calls.onRangeChange = null
  calls.indicators = []
  calls.panes = 1
}

export const CandlestickSeries = { type: 'Candlestick' }
export const HistogramSeries = { type: 'Histogram' }
export const LineSeries = { type: 'Line' }
export const ColorType = { Solid: 'solid' }
export const CrosshairMode = { Normal: 0 }
export const LineStyle = { Solid: 0, Dotted: 1, Dashed: 2 }

export const createChart = vi.fn(() => {
  let n = 0
  const panes: MockSeries[][] = [[]]
  const sync = () => (calls.panes = panes.length)
  return {
    addSeries: vi.fn((def: { type: string }, options: Record<string, unknown> = {}, paneIndex = 0) => {
      const kind = n === 0 ? 'candle' : n === 1 ? 'volume' : 'indicator'
      n++
      const s: MockSeries = { kind, type: def.type, options, paneIndex, data: [], updates: [], priceLines: [] }
      while (panes.length <= paneIndex) panes.push([])
      panes[paneIndex].push(s)
      if (kind === 'indicator') calls.indicators.push(s)
      sync()
      return {
        setData: vi.fn((d: unknown[]) => {
          s.data = d
          if (kind === 'candle') {
            calls.candleData = d
            // Like the real chart, replacing the data doesn't keep the dates in view.
            calls.visibleRange = { moved: true }
          }
          if (kind === 'volume') calls.volumeData = d
        }),
        update: vi.fn((d: unknown) => {
          s.updates.push(d)
          if (kind === 'candle') calls.updates.push(d)
        }),
        applyOptions: vi.fn((o: Record<string, unknown>) => Object.assign(s.options, o)),
        createPriceLine: vi.fn((o: unknown) => s.priceLines.push(o)),
        priceScale: () => ({ applyOptions: vi.fn() }),
        __mock: s,
      }
    }),
    removeSeries: vi.fn((api: { __mock: MockSeries }) => {
      const s = api.__mock
      panes[s.paneIndex] = panes[s.paneIndex].filter((x) => x !== s)
      calls.indicators = calls.indicators.filter((x) => x !== s)
    }),
    panes: () =>
      panes.map((p, i) => ({ getSeries: () => p, paneIndex: () => i, setStretchFactor: vi.fn(), setHeight: vi.fn() })),
    removePane: vi.fn((i: number) => {
      panes.splice(i, 1)
      sync()
    }),
    subscribeCrosshairMove: vi.fn(),
    timeScale: () => ({
      setVisibleRange: vi.fn((r: unknown) => (calls.visibleRange = r)),
      getVisibleRange: vi.fn(() => calls.visibleRange),
      setVisibleLogicalRange: vi.fn((r: unknown) => (calls.visibleRange = r)),
      subscribeVisibleLogicalRangeChange: vi.fn((cb: (range: { from: number; to: number }) => void) => {
        calls.onRangeChange = cb
      }),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      scrollToRealTime: vi.fn(),
    }),
    remove: vi.fn(),
  }
})

export const createSeriesMarkers = vi.fn(() => ({ setMarkers: vi.fn((m: unknown[]) => (calls.markers = m)) }))
