import { useAppData } from './appDataContext'
import { useCollapse } from './collapseContext'
import type { Edge } from './edges'

/** The tabs of the groups collapsed to one workspace edge. A tab shows its panel for now. */
export function EdgeStrip({ edge }: { edge: Edge }) {
  const collapse = useCollapse()
  const { tick, orders } = useAppData()
  // Counts on the tabs, so a collapsed panel still says whether it has something in it.
  const counts: Record<string, number> = {
    positions: tick?.account.positions.length ?? 0,
    orders: orders.filter((o) => o.status === 'working').length,
  }
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
                aria-label={counts[p.id] ? `${p.title}, ${counts[p.id]} ${p.id === 'orders' ? 'working' : 'open'}` : p.title}
                onClick={() => collapse.peek(g.id, p.id)}
              >
                {p.title}
                {counts[p.id] ? (
                  <span className="strip-badge" aria-hidden="true">
                    {counts[p.id]}
                  </span>
                ) : null}
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}
