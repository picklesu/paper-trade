import { useEffect, useRef, useState } from 'react'
import { useLayout } from './layoutContext'
import type { Edge } from './edges'
import { PANELS, SIDE_PANELS } from './panels'

const EDGES: [Edge, string, string][] = [
  ['left', '◀', 'Left edge'],
  ['right', '▶', 'Right edge'],
  ['top', '▲', 'Top edge'],
  ['bottom', '▼', 'Bottom edge'],
]

/** Top-bar menu that places each panel on an edge, turns it on or off, and resets the layout.
 *  The chart is always in the middle; opening and collapsing is the edge tabs' job. */
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
          {SIDE_PANELS.map((id) => {
            const place = layout.places[id]
            return (
              <div key={id} className="panel-row" role="group" aria-label={PANELS[id].title}>
                <span className="panel-row-name">{PANELS[id].title}</span>
                <div className="seg" role="radiogroup" aria-label={`${PANELS[id].title} edge`}>
                  {EDGES.map(([edge, icon, name]) => (
                    <button
                      key={edge}
                      role="radio"
                      aria-checked={place.edge === edge}
                      aria-label={name}
                      title={name}
                      className={place.edge === edge ? 'ctl icon-ctl active' : 'ctl icon-ctl'}
                      onClick={() => layout.setEdge(id, edge)}
                    >
                      {icon}
                    </button>
                  ))}
                </div>
                <button
                  role="switch"
                  aria-checked={place.state !== 'off'}
                  aria-label={`${PANELS[id].title} on`}
                  className={place.state !== 'off' ? 'ctl onoff active' : 'ctl onoff'}
                  onClick={() => layout.setOn(id, place.state === 'off')}
                >
                  {place.state !== 'off' ? 'On' : 'Off'}
                </button>
              </div>
            )
          })}
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
            Put each panel on any edge. Its tab on that edge opens or collapses it. Drag the dividers to resize.
          </p>
          <p className="menu-hint menu-credit">
            Charts by{' '}
            <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
              TradingView
            </a>
          </p>
        </div>
      )}
    </div>
  )
}
