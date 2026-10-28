import { useEffect, useRef, useState } from 'react'
import { useLayout } from './layoutContext'
import { PANEL_IDS, PANELS, type PanelState } from './panels'

const CHOICES: [PanelState, string, string][] = [
  ['open', 'Open', 'Docked beside the chart'],
  ['collapsed', 'Collapsed', 'A tab on the left edge; click it to look'],
  ['floating', 'Float', 'A window over the chart'],
  ['off', 'Off', 'Hidden'],
]
// The chart is the main view: always shown, docked or floating.
const CHART_CHOICES = CHOICES.filter(([state]) => state === 'open' || state === 'floating')

/** Top-bar menu that places each panel (open, collapsed, floating or off) and resets the layout. */
export function PanelsMenu() {
  const layout = useLayout()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!layout) return null
  return (
    <div className="panels-menu" ref={root}>
      <button className="ctl" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>
        Panels ▾
      </button>
      {open && (
        <div className="menu-panel panels-menu-panel" role="menu" aria-label="Panels">
          {PANEL_IDS.map((id) => (
            <div key={id} className="panel-row">
              <span className="panel-row-name">{PANELS[id].title}</span>
              <div className="seg" role="radiogroup" aria-label={PANELS[id].title}>
                {(id === 'chart' ? CHART_CHOICES : CHOICES).map(([state, label, hint]) => (
                  <button
                    key={state}
                    role="radio"
                    aria-checked={layout.states[id] === state}
                    className={layout.states[id] === state ? 'ctl active' : 'ctl'}
                    title={hint}
                    onClick={() => layout.setState(id, state)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <div className="menu-sep" />
          <button
            role="menuitem"
            className="menu-item"
            onClick={() => {
              layout.reset()
              setOpen(false)
            }}
          >
            Reset layout
          </button>
          <p className="menu-hint">
            Collapsed panels wait as tabs on the left edge: click one to look, click elsewhere to tuck it away. Floating
            windows move by their top bar and resize from any edge.
          </p>
        </div>
      )}
    </div>
  )
}
