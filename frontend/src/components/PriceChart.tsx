import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchBars } from '../lib/api'
import {
  applyTick,
  type Bar,
  bucketStart,
  fillMarkers,
  formatBarTime,
  formatTick,
  type Interval,
  INTERVAL_LABEL,
  INTERVALS,
  mergeBars,
  RANGES,
  type RangePreset,
  rangeStart,
} from '../lib/bars'
import { direction, percent, price, volume } from '../lib/format'
import { compute, DEFAULT_SETTINGS, indicatorLabel, INDICATORS, type IndicatorSettings, sanitize } from '../lib/indicators'
import type { ClockState, Order, Quote } from '../lib/types'
import { applicable, IndicatorLayer, lineColor } from './IndicatorLayer'
import { IndicatorMenu } from './IndicatorMenu'

interface Props {
  quote: Quote
  clock: ClockState
  orders: Order[]
  /** Progress of the background history build, if running. */
  history?: { done: number; total: number } | null
}

const UP = '#22c55e'
const DOWN = '#ef4444'
const PAGE = 500
const RESYNC_MS = 1500
const SETTLE_MS = 80
// Bars per preset load: enough to cover the range in one request.
const PRESET_COUNT: Record<string, number> = { '1D': 400, '5D': 420, '1M': 300, '3M': 460, '6M': 140, YTD: 260, '1Y': 260, '5Y': 270 }

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

const SETTINGS_KEY = 'paper-trade.indicators'

/** Indicator choices are a per-browser convenience; storage may be unavailable. */
function loadSettings(): IndicatorSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    return raw ? sanitize(JSON.parse(raw)) : structuredClone(DEFAULT_SETTINGS)
  } catch {
    return structuredClone(DEFAULT_SETTINGS)
  }
}

function saveSettings(settings: IndicatorSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    // Not saved; the choice still applies until the page reloads.
  }
}

function candle(b: Bar) {
  return { time: b[0] as UTCTimestamp, open: b[1], high: b[2], low: b[3], close: b[4] }
}

function volumeBar(b: Bar) {
  return { time: b[0] as UTCTimestamp, value: b[5], color: b[4] >= b[1] ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)' }
}

/** Interactive candlestick chart: drag to pan, scroll or pinch to zoom, older history loads as you scroll back. */
export function PriceChart({ quote, clock, orders, history }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumes = useRef<ISeriesApi<'Histogram'> | null>(null)
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const indicators = useRef<IndicatorLayer | null>(null)
  const [settings, setSettings] = useState<IndicatorSettings>(loadSettings)

  const [interval, setInterval_] = useState<Interval>('5m')
  const [preset, setPreset] = useState<RangePreset | null>(RANGES[1])
  const [bars, setBars] = useState<Bar[]>([])
  const [hover, setHover] = useState<Bar | null>(null)
  // What finished loading (or failed), so "loading" is simply "not the current view yet".
  const [loaded, setLoaded] = useState<{ key: string; error: string | null } | null>(null)

  // Mutable mirrors for chart callbacks, which outlive renders.
  const barsRef = useRef<Bar[]>([])
  const moreRef = useRef(true)
  const loadingOlder = useRef(false)
  // Bumped on every full load; late responses from an earlier load are ignored.
  const generation = useRef(0)
  // Which view (symbol|interval|timeline) the bars on screen belong to.
  const shownView = useRef<string | null>(null)
  // False from a full load until its visible range has been applied (the chart applies
  // ranges on its next frame); older pages wait for this so they shift the right view.
  const settled = useRef(false)
  const loadOlderIfNeeded = useRef<() => void>(() => {})

  const symbol = quote.symbol
  const now = Date.parse(clock.time) / 1000
  const sessionOpen = Date.parse(clock.session_open) / 1000

  const viewKey = `${symbol}|${interval}|${clock.timeline}`
  const loading = loaded?.key !== viewKey
  const error = loaded?.key === viewKey ? loaded.error : null

  const intervalRef = useRef(interval)
  const sessionOpenRef = useRef(sessionOpen)
  const presetRef = useRef(preset)
  useEffect(() => {
    intervalRef.current = interval
    sessionOpenRef.current = sessionOpen
    presetRef.current = preset
  })

  const show = (next: Bar[]) => {
    barsRef.current = next
    setBars(next)
    candles.current?.setData(next.map(candle))
    volumes.current?.setData(next.map(volumeBar))
    indicators.current?.setData(next)
  }

  // Create the chart once.
  useEffect(() => {
    const el = box.current!
    const c = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: css('--panel') || '#161b22' }, textColor: css('--muted') || '#7d8590' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: css('--border') || '#2a313c' },
      timeScale: {
        borderColor: css('--border') || '#2a313c',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: (t: Time, type: number) => formatTick(t as number, type),
      },
      localization: { timeFormatter: (t: Time) => formatBarTime(t as number, intervalRef.current) },
    })
    candles.current = c.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
    })
    candles.current.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } })
    volumes.current = c.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'volume' })
    volumes.current.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
    markers.current = createSeriesMarkers(candles.current, [])
    indicators.current = new IndicatorLayer(c)

    c.subscribeCrosshairMove((param) => {
      const t = param.time as number | undefined
      setHover(t === undefined ? null : (barsRef.current.find((b) => b[0] === t) ?? null))
    })
    chart.current = c
    return () => {
      c.remove()
      chart.current = candles.current = volumes.current = markers.current = indicators.current = null
    }
  }, [])

  // Indicator series follow the settings and the bar size (VWAP is intraday only). Their data
  // comes with the bars: only fill them now if the bars on screen are already this view's.
  useEffect(() => {
    indicators.current?.configure(settings, interval)
    if (shownView.current === `${symbol}|${interval}|${clock.timeline}`) indicators.current?.setData(barsRef.current)
    // Only settings and bar size reconfigure; symbol and timeline changes reload the bars (show()).
  }, [settings, interval]) // eslint-disable-line react-hooks/exhaustive-deps

  const changeSettings = (next: IndicatorSettings) => {
    setSettings(next)
    saveSettings(next)
  }

  // (Re)load when the symbol, bar size or timeline (a jump, rewind or new history) changes.
  useEffect(() => {
    let cancelled = false
    const key = `${symbol}|${interval}|${clock.timeline}`
    const gen = ++generation.current
    settled.current = false
    // The preset chose this bar size; read it via the ref so changing only the preset
    // moves the view (choosePreset) instead of reloading.
    const preset = presetRef.current
    const count = preset ? (PRESET_COUNT[preset.label] ?? PAGE) : PAGE
    fetchBars(symbol, interval, undefined, count)
      .then((page) => {
        if (cancelled || gen !== generation.current) return
        moreRef.current = page.more
        show(page.bars)
        shownView.current = key
        setLoaded({ key, error: null })
        const ts = chart.current?.timeScale()
        if (!ts || !page.bars.length) return
        if (preset) {
          const last = page.bars[page.bars.length - 1][0]
          ts.setVisibleRange({ from: rangeStart(preset, last, sessionOpenRef.current) as UTCTimestamp, to: last as UTCTimestamp })
        } else {
          ts.setVisibleLogicalRange({ from: page.bars.length - 150, to: page.bars.length + 4 })
        }
        // Once the chart has applied that range, load older bars if the view needs them.
        window.setTimeout(() => {
          if (gen !== generation.current) return
          settled.current = true
          loadOlderIfNeeded.current()
        }, SETTLE_MS)
      })
      .catch((err) => !cancelled && setLoaded({ key, error: err instanceof Error ? err.message : String(err) }))
    return () => {
      cancelled = true
    }
  }, [symbol, interval, clock.timeline])

  // Load older bars when the user scrolls or zooms out near the start.
  useEffect(() => {
    const ts = chart.current?.timeScale()
    if (!ts) return
    const onRange = (range: { from: number; to: number } | null) => {
      if (!settled.current) return // a fresh load's view is still being applied
      if (!range || range.from > 20 || !moreRef.current || loadingOlder.current || !barsRef.current.length) return
      loadingOlder.current = true
      const first = barsRef.current[0][0]
      const gen = generation.current
      fetchBars(symbol, intervalRef.current, first, PAGE)
        .then((page) => {
          if (gen !== generation.current) return // the chart reloaded meanwhile (symbol, size, jump)
          moreRef.current = page.more
          // Inserting bars on the left would slide the view back in time (the chart keeps
          // bar positions, not dates), which then triggers yet another load. Shift the view
          // by the number of bars added, which keeps zoom, position and right margin exactly.
          const ts = chart.current?.timeScale()
          const view = ts?.getVisibleLogicalRange()
          const before = barsRef.current.length
          show(mergeBars(page.bars, barsRef.current))
          const added = barsRef.current.length - before
          if (ts && view) ts.setVisibleLogicalRange({ from: view.from + added, to: view.to + added })
        })
        .catch(() => {
          moreRef.current = false
        })
        .finally(() => {
          loadingOlder.current = false
        })
    }
    loadOlderIfNeeded.current = () => onRange(ts.getVisibleLogicalRange())
    ts.subscribeVisibleLogicalRangeChange(onRange)
    return () => ts.unsubscribeVisibleLogicalRangeChange(onRange)
  }, [symbol])

  // Live: fold each tick into the last bar right away...
  useEffect(() => {
    const current = barsRef.current
    // Only once the bars on screen are this view's (not while a new size or timeline loads).
    if (shownView.current !== viewKey || !current.length || !candles.current) return
    const last = current[current.length - 1]
    const bucket = bucketStart(interval, now, sessionOpen, last[0])
    const next = applyTick(current, bucket, quote.last)
    if (next === current) return
    barsRef.current = next
    const bar = next[next.length - 1]
    candles.current.update(candle(bar))
    volumes.current?.update(volumeBar(bar))
    indicators.current?.updateLast(next)
    setBars(next) // the legend shows the latest bar and its indicator values
  }, [quote.last, now, sessionOpen, interval, viewKey])

  // ...and resync the latest two bars from the server now and then, for exact volume
  // and the bar that just closed.
  useEffect(() => {
    if (clock.paused) return
    const id = window.setInterval(() => {
      if (!barsRef.current.length) return
      const gen = generation.current
      fetchBars(symbol, intervalRef.current, undefined, 2)
        .then((page) => {
          if (gen !== generation.current) return // the chart reloaded meanwhile
          // Merge into whatever is on screen now (ticks may have updated it since).
          const current = barsRef.current
          const fresh = page.bars.filter((b) => b[0] >= current[0][0])
          const changed = fresh.some((b) => {
            const mine = current.find((c) => c[0] === b[0])
            return !mine || mine.some((v, i) => v !== b[i])
          })
          if (changed) show(mergeBars(current, fresh))
        })
        .catch(() => {})
    }, RESYNC_MS)
    return () => window.clearInterval(id)
  }, [symbol, clock.paused])

  // Your fills on the chart.
  const fills = useMemo(() => fillMarkers(bars, orders, symbol), [bars, orders, symbol])
  useEffect(() => {
    markers.current?.setMarkers(
      fills.map((m) => ({
        time: m.time as UTCTimestamp,
        position: m.side === 'buy' ? 'belowBar' : 'aboveBar',
        color: m.side === 'buy' ? UP : DOWN,
        shape: m.side === 'buy' ? 'arrowUp' : 'arrowDown',
        text: m.text,
      })),
    )
  }, [fills])

  const choosePreset = (p: RangePreset) => {
    setPreset(p)
    if (p.interval === interval) {
      // Same bar size: just move the view (loading more first if needed).
      const ts = chart.current?.timeScale()
      const last = barsRef.current[barsRef.current.length - 1]?.[0]
      if (ts && last) ts.setVisibleRange({ from: rangeStart(p, last, sessionOpen) as UTCTimestamp, to: last as UTCTimestamp })
    } else {
      setInterval_(p.interval)
    }
  }

  const chooseInterval = (iv: Interval) => {
    setPreset(null)
    setInterval_(iv)
  }

  const legend = hover ?? bars[bars.length - 1] ?? null

  // Indicator series computed once per bars/settings; the legend just reads its bar's index.
  const indicatorSeries = useMemo(
    () =>
      INDICATORS.filter((i) => settings[i.id].on && applicable(i.id, interval)).map((info) => ({
        id: info.id,
        label: indicatorLabel(info.id, settings[info.id]),
        lines: compute(info.id, settings[info.id].params, bars).lines,
      })),
    [bars, settings, interval],
  )
  const legendIndex = legend ? bars.findIndex((b) => b[0] === legend[0]) : -1
  const indicatorValues =
    legendIndex < 0
      ? []
      : indicatorSeries.map((s) => ({
          id: s.id,
          label: s.label,
          values: s.lines.map(({ key, values }) => ({ key, value: values[legendIndex], color: lineColor(s.id, key) })),
        }))

  return (
    <div className="price-chart">
      <div className="chart-toolbar">
        <div className="seg" role="group" aria-label="Range">
          {RANGES.map((r) => (
            <button
              key={r.label}
              className={preset?.label === r.label ? 'ctl active' : 'ctl'}
              aria-pressed={preset?.label === r.label}
              title={`Show ${r.label === 'YTD' ? 'year to date' : r.label}`}
              onClick={() => choosePreset(r)}
            >
              {r.label}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Bar size">
          {INTERVALS.map((iv) => (
            <button
              key={iv}
              className={interval === iv ? 'ctl active' : 'ctl'}
              aria-pressed={interval === iv}
              aria-label={INTERVAL_LABEL[iv].long}
              title={INTERVAL_LABEL[iv].long}
              onClick={() => chooseInterval(iv)}
            >
              {INTERVAL_LABEL[iv].short}
            </button>
          ))}
        </div>
        <IndicatorMenu settings={settings} interval={interval} onChange={changeSettings} />
        <button className="ctl" onClick={() => chart.current?.timeScale().scrollToRealTime()} title="Scroll to the latest bar">
          Latest ⇥
        </button>
      </div>
      <div className="chart-area">
        <div ref={box} className="chart-canvas" data-testid="chart" />
        {legend && (
          <div className="chart-legend" aria-label="Bar details">
            <span className="muted">{formatBarTime(legend[0], interval)}</span>
            <span>O {price(legend[1])}</span>
            <span>H {price(legend[2])}</span>
            <span>L {price(legend[3])}</span>
            <span className={direction(legend[4] - legend[1])}>C {price(legend[4])}</span>
            <span className={direction(legend[4] - legend[1])}>{percent(((legend[4] - legend[1]) / legend[1]) * 100)}</span>
            <span className="muted">Vol {volume(legend[5])}</span>
            {indicatorValues.length > 0 && (
              <span className="legend-indicators">
                {indicatorValues.map((iv) => (
                  <span key={iv.id}>
                    <span className="muted">{iv.label}</span>{' '}
                    {iv.values.map((v) => (
                      <span key={v.key} style={{ color: v.color }}>
                        {v.value === null ? '–' : price(v.value)}{' '}
                      </span>
                    ))}
                  </span>
                ))}
              </span>
            )}
          </div>
        )}
        {(loading || error || clock.building_history) && (
          <div className="chart-status">
            {error
              ? `Couldn't load the chart: ${error}`
              : clock.building_history
                ? `Building 5 years of price history…${history ? ` ${Math.round((history.done / history.total) * 100)}%` : ''}`
                : 'Loading…'}
          </div>
        )}
      </div>
    </div>
  )
}
