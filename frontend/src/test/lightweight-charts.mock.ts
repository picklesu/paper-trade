// Test stand-in for lightweight-charts (it draws on <canvas>, which jsdom lacks).
// Records what the component asks of it so tests can check data, markers and ranges.
import { vi } from 'vitest'

export const calls = {
  candleData: [] as unknown[],
  volumeData: [] as unknown[],
  markers: [] as unknown[],
  visibleRange: null as unknown,
  updates: [] as unknown[],
}

export function resetCalls() {
  calls.candleData = []
  calls.volumeData = []
  calls.markers = []
  calls.visibleRange = null
  calls.updates = []
}

function series(kind: 'candle' | 'volume') {
  return {
    setData: vi.fn((d: unknown[]) => {
      if (kind === 'candle') calls.candleData = d
      else calls.volumeData = d
    }),
    update: vi.fn((d: unknown) => kind === 'candle' && calls.updates.push(d)),
    priceScale: () => ({ applyOptions: vi.fn() }),
  }
}

export const createChart = vi.fn(() => {
  let n = 0
  return {
    addSeries: vi.fn(() => series(n++ === 0 ? 'candle' : 'volume')),
    subscribeCrosshairMove: vi.fn(),
    timeScale: () => ({
      setVisibleRange: vi.fn((r: unknown) => (calls.visibleRange = r)),
      setVisibleLogicalRange: vi.fn((r: unknown) => (calls.visibleRange = r)),
      subscribeVisibleLogicalRangeChange: vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      scrollToRealTime: vi.fn(),
    }),
    remove: vi.fn(),
  }
})

export const createSeriesMarkers = vi.fn(() => ({ setMarkers: vi.fn((m: unknown[]) => (calls.markers = m)) }))
export const CandlestickSeries = {}
export const HistogramSeries = {}
export const ColorType = { Solid: 'solid' }
export const CrosshairMode = { Normal: 0 }
