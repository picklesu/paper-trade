import type { DockviewApi } from 'dockview-react'
import { useEffect, useMemo, useState } from 'react'
import type { StripControls, StripTab } from './collapseContext'
import { edgeOf } from './edges'
import { PANELS, type PanelId, SIDE_PANELS, showGroup } from './panels'

/** The edge strips, like VS Code's side bars: a tab for every panel on an edge, which opens
 *  or collapses it. A collapsed panel is a hidden grid group (dockview saves that with the
 *  layout, so it stays collapsed across reloads). */
export function useEdgeStrips(api: DockviewApi | null): StripControls {
  const [tabs, setTabs] = useState<StripTab[]>([])

  useEffect(() => {
    if (!api) return
    const sync = () => {
      const layout = api.toJSON()
      const chartId = api.getPanel('chart')?.group.id
      const next: StripTab[] = []
      for (const id of SIDE_PANELS) {
        const group = api.getPanel(id)?.group
        if (!group || group.api.location.type !== 'grid') continue
        next.push({ groupId: group.id, panelId: id, title: PANELS[id as PanelId].title, edge: edgeOf(layout, group.id, chartId), open: group.api.isVisible })
      }
      // Only when something changed: layout changes come in on every divider drag.
      setTabs((current) => (JSON.stringify(current) === JSON.stringify(next) ? current : next))
    }
    sync()
    const subs = [api.onDidLayoutChange(sync), api.onDidAddPanel(sync), api.onDidRemovePanel(sync), api.onDidLayoutFromJSON(sync)]
    return () => subs.forEach((s) => s.dispose())
  }, [api])

  return useMemo<StripControls>(
    () => ({
      tabs,
      toggle: (groupId) => {
        // Not api.getGroup: its type lacks what showGroup needs.
        const group = api?.groups.find((g) => g.id === groupId)
        if (!api || !group) return
        if (group.api.isVisible) group.api.setVisible(false)
        else showGroup(api, group)
      },
    }),
    [api, tabs],
  )
}
