import { useAppData } from './appDataContext'
import { useStrips } from './collapseContext'
import type { Edge } from './edges'

/** The tabs of the panels on one workspace edge. A tab opens its panel, or collapses it. */
export function EdgeStrip({ edge }: { edge: Edge }) {
  const strips = useStrips()
  const { tick, orders } = useAppData()
  // Counts on the tabs, so a collapsed panel still says whether it has something in it.
  const counts: Record<string, number> = {
    positions: tick?.account.positions.length ?? 0,
    orders: orders.filter((o) => o.status === 'working').length,
  }
  const tabs = strips?.tabs.filter((t) => t.edge === edge) ?? []
  if (!strips || !tabs.length) return null
  return (
    <div className={`edge-strip edge-${edge}`} role="toolbar" aria-label={`Panels (${edge})`}>
      {tabs.map((t) => (
        <button
          key={t.panelId}
          className="strip-tab"
          aria-pressed={t.open}
          title={t.open ? `Collapse ${t.title}` : `Open ${t.title}`}
          aria-label={counts[t.panelId] ? `${t.title}, ${counts[t.panelId]} ${t.panelId === 'orders' ? 'working' : 'open'}` : t.title}
          onClick={() => strips.toggle(t.groupId)}
        >
          {t.title}
          {counts[t.panelId] ? (
            <span className="strip-badge" aria-hidden="true">
              {counts[t.panelId]}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  )
}
