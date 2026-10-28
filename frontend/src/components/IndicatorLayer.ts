import {
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Bar, Interval } from '../lib/bars'
import { compute, type IndicatorId, INDICATORS, type IndicatorSettings, type Values } from '../lib/indicators'

/** Line colors per indicator and line. */
export const COLORS: Record<string, string> = {
  sma20: '#f59e0b',
  sma50: '#3b82f6',
  sma200: '#e5e7eb',
  ema9: '#a855f7',
  ema21: '#ec4899',
  vwap: '#14b8a6',
  'bb.upper': '#94a3b8',
  'bb.middle': '#64748b',
  'bb.lower': '#94a3b8',
  rsi: '#a78bfa',
  'macd.macd': '#3b82f6',
  'macd.signal': '#f97316',
}

export const lineColor = (id: IndicatorId, key: string) => COLORS[`${id}.${key}`] ?? COLORS[id] ?? '#e5e7eb'

/** Which indicators apply to a bar size (VWAP restarts each session, so it's intraday only). */
export function applicable(id: IndicatorId, interval: Interval): boolean {
  const info = INDICATORS.find((i) => i.id === id)!
  return !info.intradayOnly || (interval !== '1D' && interval !== '1W')
}

interface Drawn {
  lines: Map<string, ISeriesApi<'Line'>>
  histogram?: ISeriesApi<'Histogram'>
  params: number[]
}

function points(bars: Bar[], values: Values) {
  // Bars without a value yet become whitespace, keeping the time axis aligned.
  return bars.map((b, i) => (values[i] === null ? { time: b[0] as UTCTimestamp } : { time: b[0] as UTCTimestamp, value: values[i]! }))
}

/** Owns the indicator series on a chart: overlays on the price pane, RSI/MACD in panes below. */
export class IndicatorLayer {
  private drawn = new Map<IndicatorId, Drawn>()
  private readonly chart: IChartApi

  constructor(chart: IChartApi) {
    this.chart = chart
  }

  /** Recreate series for the enabled indicators. */
  configure(settings: IndicatorSettings, interval: Interval): void {
    this.clear()
    let pane = 1
    for (const info of INDICATORS) {
      const s = settings[info.id]
      if (!s.on || !applicable(info.id, interval)) continue
      const paneIndex = info.pane === 'own' ? pane++ : 0
      const keys = info.lines
      const lines = new Map<string, ISeriesApi<'Line'>>()
      let histogram: ISeriesApi<'Histogram'> | undefined
      if (info.id === 'macd') {
        histogram = this.chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, paneIndex)
      }
      for (const key of keys) {
        const line = this.chart.addSeries(
          LineSeries,
          {
            color: lineColor(info.id, key),
            lineWidth: key === 'middle' ? 1 : 2,
            lineStyle: key === 'middle' ? LineStyle.Dashed : LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: info.pane === 'own',
            crosshairMarkerVisible: false,
          },
          paneIndex,
        )
        lines.set(key, line)
      }
      if (info.id === 'rsi') {
        const rsiLine = lines.get('value')!
        rsiLine.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) })
        for (const level of [70, 30]) {
          rsiLine.createPriceLine({ price: level, color: '#7d8590', lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: true, title: '' })
        }
      }
      this.drawn.set(info.id, { lines, histogram, params: s.params })
    }
    // The price pane gets most of the height; indicator panes share the rest.
    const panes = this.chart.panes()
    panes.forEach((p, i) => p.setStretchFactor(i === 0 ? 4 : 1))
  }

  /** Recompute everything from the bars (after a load). */
  setData(bars: Bar[]): void {
    for (const [id, d] of this.drawn) {
      const out = compute(id, d.params, bars)
      for (const { key, values } of out.lines) d.lines.get(key)?.setData(points(bars, values))
      if (d.histogram && out.histogram) {
        d.histogram.setData(
          bars.map((b, i) => {
            const v = out.histogram![i]
            return v === null ? { time: b[0] as UTCTimestamp } : { time: b[0] as UTCTimestamp, value: v, color: v >= 0 ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.6)' }
          }),
        )
      }
    }
  }

  /** Update just the last point (after a live tick changed or added the last bar). */
  updateLast(bars: Bar[]): void {
    const last = bars[bars.length - 1]
    if (!last) return
    const time = last[0] as UTCTimestamp
    for (const [id, d] of this.drawn) {
      const out = compute(id, d.params, bars)
      for (const { key, values } of out.lines) {
        const v = values[values.length - 1]
        d.lines.get(key)?.update(v === null ? { time } : { time, value: v })
      }
      if (d.histogram && out.histogram) {
        const v = out.histogram[out.histogram.length - 1]
        d.histogram.update(v === null ? { time } : { time, value: v, color: v >= 0 ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.6)' })
      }
    }
  }

  clear(): void {
    for (const d of this.drawn.values()) {
      for (const line of d.lines.values()) this.chart.removeSeries(line)
      if (d.histogram) this.chart.removeSeries(d.histogram)
    }
    this.drawn.clear()
    // Drop indicator panes left empty (the price pane, index 0, always stays).
    const panes = this.chart.panes()
    for (let i = panes.length - 1; i >= 1; i--) {
      if (panes[i].getSeries().length === 0) this.chart.removePane(i)
    }
  }
}
