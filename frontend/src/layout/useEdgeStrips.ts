import type { DockviewApi } from 'dockview-react'
import { type RefObject, useEffect, useMemo, useRef, useState } from 'react'
import type { CollapseControls, CollapsedGroup } from './collapseContext'
import { type Edge, edgeOf } from './edges'
import { PANELS, type PanelId } from './panels'

const titleOf = (id: string) => PANELS[id as PanelId]?.title ?? id
const groupOf = (api: DockviewApi, id: string) => api.groups.find((g) => g.id === id)

/** Collapsing docked groups to edge strips, like VS Code's side bars. A collapsed group is
 *  a hidden grid group (dockview saves that with the layout, so it stays collapsed across
 *  reloads); peeking shows it until the next click elsewhere in the workspace. */
export function useEdgeStrips(api: DockviewApi | null, frame: RefObject<HTMLElement | null>): CollapseControls & { peekRef: RefObject<string | null> } {
  const [collapsed, setCollapsed] = useState<CollapsedGroup[]>([])
  const [edges, setEdges] = useState<ReadonlyMap<string, Edge>>(new Map())
  const [peeking, setPeeking] = useState<string | null>(null)
  // The workspace reads this when saving, so a peeked group is saved collapsed.
  const peekRef = useRef<string | null>(null)
  const sync = useRef<() => void>(() => {})

  const setPeek = (id: string | null) => {
    peekRef.current = id
    setPeeking(id)
  }

  useEffect(() => {
    if (!api) return
    sync.current = () => {
      // While a group is maximized every other group is hidden too; keep the strips as they were.
      if (api.hasMaximizedGroup()) return
      const layout = api.toJSON()
      const chart = api.getPanel('chart')?.group
      const chartId = chart?.api.location.type === 'grid' ? chart.id : undefined
      const nextEdges = new Map<string, Edge>()
      const next: CollapsedGroup[] = []
      for (const g of api.groups) {
        if (g.api.location.type !== 'grid') continue
        const edge = edgeOf(layout, g.id, chartId)
        nextEdges.set(g.id, edge)
        if (!g.api.isVisible || g.id === peekRef.current) {
          next.push({ id: g.id, edge, panels: g.panels.map((p) => ({ id: p.id, title: titleOf(p.id) })), activePanel: g.activePanel?.id })
        }
      }
      if (peekRef.current && !next.some((c) => c.id === peekRef.current)) setPeek(null)
      setEdges(nextEdges)
      setCollapsed(next)
    }
    sync.current()
    const run = () => sync.current()
    const subs = [
      api.onDidLayoutChange(run),
      api.onDidAddPanel(run),
      api.onDidRemovePanel(run),
      api.onDidActivePanelChange(run),
      api.onDidLayoutFromJSON(run),
      api.onDidMaximizedGroupChange(run),
    ]
    return () => subs.forEach((s) => s.dispose())
  }, [api])

  // A click anywhere else in the workspace collapses the peeked group again. Clicks outside
  // the workspace (the top bar, dialogs) leave it open.
  useEffect(() => {
    if (!api || !peeking) return
    const onDown = (e: PointerEvent) => {
      const target = e.target as Element
      const group = groupOf(api, peeking)
      if (!frame.current?.contains(target) || group?.element.contains(target) || target.closest('.edge-strip')) return
      setPeek(null)
      group?.api.setVisible(false)
      sync.current()
    }
    document.addEventListener('pointerdown', onDown, true)
    groupOf(api, peeking)?.element.classList.add('peeking')
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      groupOf(api, peeking)?.element.classList.remove('peeking')
    }
  }, [api, peeking, frame])

  const controls = useMemo<CollapseControls>(
    () => ({
      collapsed,
      peeking,
      edges,
      collapse: (group) => {
        if (peekRef.current === group.id) setPeek(null)
        group.api.setVisible(false)
        sync.current()
      },
      peek: (groupId, panelId) => {
        const group = api && groupOf(api, groupId)
        if (!api || !group) return
        if (peekRef.current === groupId && group.activePanel?.id === panelId) {
          // The same tab again: collapse.
          setPeek(null)
          group.api.setVisible(false)
        } else {
          if (peekRef.current && peekRef.current !== groupId) groupOf(api, peekRef.current)?.api.setVisible(false)
          setPeek(groupId)
          group.api.setVisible(true)
          api.getPanel(panelId)?.api.setActive()
        }
        sync.current()
      },
      pin: () => {
        setPeek(null)
        sync.current()
      },
    }),
    [api, collapsed, peeking, edges],
  )
  return { ...controls, peekRef }
}
