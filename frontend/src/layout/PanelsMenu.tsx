import { useEffect, useRef, useState } from 'react'
import { PANEL_IDS, PANELS } from './panels'
import { useLayout } from './layoutContext'

/** Top-bar menu to show or hide panels and reset the layout. */
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
        <div className="menu-panel" role="menu" aria-label="Panels">
          {PANEL_IDS.map((id) => (
            <button
              key={id}
              role="menuitemcheckbox"
              aria-checked={layout.open.has(id)}
              className="menu-item"
              onClick={() => layout.toggle(id)}
            >
              <span className="check">{layout.open.has(id) ? '✓' : ''}</span>
              {PANELS[id].title}
            </button>
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
            <span className="check" />
            Reset layout
          </button>
          <p className="menu-hint">
            Drag a tab to move a panel: drop on an edge to dock it there, or onto another panel to tab them together.
            ⧉ pops a panel out as a floating window; ⤢ maximizes it; ⇤ ⇥ ⤓ collapse it to a strip on that edge.
          </p>
        </div>
      )}
    </div>
  )
}
