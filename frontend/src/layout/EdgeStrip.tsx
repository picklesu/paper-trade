import { useCollapse } from './collapseContext'
import type { Edge } from './edges'

/** The tabs of the groups collapsed to one workspace edge. A tab shows its panel for now. */
export function EdgeStrip({ edge }: { edge: Edge }) {
  const collapse = useCollapse()
  const groups = collapse?.collapsed.filter((g) => g.edge === edge) ?? []
  if (!collapse || !groups.length) return null
  return (
    <div className={`edge-strip edge-${edge}`} role="toolbar" aria-label={`Collapsed panels (${edge})`}>
      {groups.map((g) => (
        <div key={g.id} className="strip-group">
          {g.panels.map((p) => {
            const shown = collapse.peeking === g.id && g.activePanel === p.id
            return (
              <button
                key={p.id}
                className="strip-tab"
                aria-pressed={shown}
                title={shown ? `Hide ${p.title}` : `Show ${p.title}`}
                onClick={() => collapse.peek(g.id, p.id)}
              >
                {p.title}
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}
