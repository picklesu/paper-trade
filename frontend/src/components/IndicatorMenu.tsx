import { useEffect, useRef, useState } from 'react'
import type { Interval } from '../lib/bars'
import { clampParam, type IndicatorId, INDICATORS, type IndicatorSettings, normalizeParams } from '../lib/indicators'
import { applicable, lineColor } from './IndicatorLayer'

interface Props {
  settings: IndicatorSettings
  interval: Interval
  onChange: (settings: IndicatorSettings) => void
}

/** Toolbar button with a panel to turn indicators on and off and set their lengths. */
export function IndicatorMenu({ settings, interval, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  // Count what's drawn: VWAP, for one, doesn't apply to daily or weekly bars.
  const count = INDICATORS.filter((i) => settings[i.id].on && applicable(i.id, interval)).length

  // Close when clicking elsewhere or pressing Escape.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const set = (id: IndicatorId, change: Partial<{ on: boolean; params: number[] }>) =>
    onChange({ ...settings, [id]: { ...settings[id], ...change } })

  return (
    <div className="indicator-menu" ref={root}>
      <button className={count ? 'ctl active' : 'ctl'} aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>
        Indicators{count ? ` (${count})` : ''} ▾
      </button>
      {open && (
        <div className="indicator-panel" role="group" aria-label="Indicators">
          {INDICATORS.map((info) => {
            const s = settings[info.id]
            const usable = applicable(info.id, interval)
            const label = info.name
            return (
              <div key={info.id} className={usable ? 'indicator-row' : 'indicator-row disabled'}>
                <label className="indicator-name">
                  <input
                    type="checkbox"
                    checked={s.on}
                    onChange={(e) => set(info.id, { on: e.target.checked })}
                    aria-label={`${label}${s.params.length ? ` ${s.params.join(' ')}` : ''}`}
                  />
                  <span className="swatch" style={{ background: lineColor(info.id, info.lines[0]) }} />
                  {label}
                </label>
                <span className="indicator-params">
                  {info.paramNames.map((name, i) => (
                    <input
                      // Keyed by value so the box shows a corrected value (e.g. MACD lengths swapped).
                      key={`${name}-${s.params[i]}`}
                      type="number"
                      className="param"
                      title={name}
                      aria-label={`${label} ${name.toLowerCase()}`}
                      step={info.id === 'bb' && i === 1 ? 0.1 : 1}
                      defaultValue={s.params[i]}
                      onBlur={(e) => {
                        const v = clampParam(info.id, i, e.target.value, s.params[i])
                        const params = normalizeParams(info.id, s.params.map((p, j) => (j === i ? v : p)))
                        e.target.value = String(params[i])
                        if (params.some((p, j) => p !== s.params[j])) set(info.id, { params })
                      }}
                      onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                    />
                  ))}
                  {!usable && <span className="muted small">intraday bars only</span>}
                </span>
              </div>
            )
          })}
          <p className="muted small">RSI and MACD appear in their own panes below the price.</p>
        </div>
      )}
    </div>
  )
}
