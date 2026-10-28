import {
  type DockviewApi,
  type DockviewGroupPanel,
  type DockviewReadyEvent,
  DockviewReact,
  type FloatingGroupDragContext,
  type SerializedDockview,
  themeDark,
} from 'dockview-react'
import 'dockview-react/dist/styles/dockview.css'
import { memo, type ReactNode, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useAppData } from './appDataContext'
import { type CollapseControls, CollapseContext, CollapseSetterContext } from './collapseContext'
import { EdgeStrip } from './EdgeStrip'
import { type Edge, edgeOf, savedSizes, withHidden } from './edges'
import { ApiSetterContext, type LayoutControls, LayoutContext } from './layoutContext'
import { clearLayout, loadLayout, saveLayout } from './layoutStorage'
import { buildDefaultLayout, collapseEmptySideColumn, COMPONENTS, dockPanel, floatGroup, keepChartSpace, mainGroup, PANEL_IDS, type PanelId, panelState, type PanelState, sideColumnTidier } from './panels'
import { snapFloatingResizes } from './floatingResize'
import { snapPosition } from './snap'
import { useEdgeStrips } from './useEdgeStrips'

/** Panel titles with live counts, like a broker's "Orders (2 working)", shown on floating
 *  windows' bars. Its own component so only it re-renders on every market tick. */
function LiveTitles({ api }: { api: DockviewApi }) {
  const { tick, orders } = useAppData()
  const positions = tick?.account.positions.length ?? 0
  const working = orders.filter((o) => o.status === 'working').length
  useEffect(() => {
    const update = () => {
      api.getPanel('positions')?.api.setTitle(positions ? `Positions (${positions})` : 'Positions')
      api.getPanel('orders')?.api.setTitle(working ? `Orders (${working} working)` : 'Orders')
      api.groups.forEach(labelFloatingWindow)
    }
    update()
    // A panel brought back starts with its plain title.
    const sub = api.onDidAddPanel(update)
    return () => sub.dispose()
  }, [api, positions, working])
  return null
}

function readStates(api: DockviewApi | null, peeking: string | null): Record<PanelId, PanelState> {
  return Object.fromEntries(PANEL_IDS.map((id) => [id, api ? panelState(api, id, peeking) : 'off'])) as Record<PanelId, PanelState>
}

/** Holds the dockview API (and the workspace's collapse controls) so the Panels menu,
 *  outside the workspace, can drive each panel. */
export function LayoutProvider({ children }: { children: ReactNode }) {
  const [api, setApi] = useState<DockviewApi | null>(null)
  const [collapse, setCollapse] = useState<CollapseControls | null>(null)
  const peeking = collapse?.peeking ?? null
  const [states, setStates] = useState<Record<PanelId, PanelState>>(() => readStates(null, null))

  useEffect(() => {
    if (!api) return
    // Only when a state changes: layout changes come in on every divider drag.
    const sync = () => {
      const next = readStates(api, peeking)
      setStates((current) => (PANEL_IDS.every((id) => current[id] === next[id]) ? current : next))
    }
    sync()
    const subs = [api.onDidAddPanel(sync), api.onDidRemovePanel(sync), api.onDidLayoutChange(sync), api.onDidLayoutFromJSON(sync)]
    return () => subs.forEach((s) => s.dispose())
  }, [api, peeking])

  const controls = useMemo<LayoutControls>(() => {
    return {
      states,
      setState: (id, state) => {
        if (!api) return
        const panel = api.getPanel(id)
        if (state === 'off') panel?.api.close()
        else if (state === 'floating') {
          const p = panel ?? dockPanel(api, id)
          if (p.group.api.location.type === 'grid') floatGroup(api, p.group)
        } else {
          const p = dockPanel(api, id, state === 'open')
          if (state === 'collapsed') collapse?.collapse(p.group)
          else if (peeking === p.group.id) collapse?.pin()
        }
      },
      reset: () => {
        if (!api) return
        // Drop any peek first: the new layout reuses group ids.
        collapse?.pin()
        clearLayout()
        buildDefaultLayout(api)
      },
    }
  }, [api, collapse, peeking, states])

  return (
    <LayoutContext.Provider value={controls}>
      <ApiSetterContext.Provider value={setApi}>
        <CollapseSetterContext.Provider value={setCollapse}>{children}</CollapseSetterContext.Provider>
      </ApiSetterContext.Provider>
      {api && <LiveTitles api={api} />}
    </LayoutContext.Provider>
  )
}

const SAVE_DELAY_MS = 300
const EDGES: Edge[] = ['top', 'left', 'right', 'bottom']

/** Floating windows snap to the workspace edges and to each other. A module-level function:
 *  DockviewReact passes any changed option prop to `updateOptions`, which lays the whole grid
 *  out again from the sizes saved before a divider drag, so a new function on each render
 *  snapped a panel being resized back to where it started. */
const snapFloatingDrag = (ctx: FloatingGroupDragContext) => snapPosition(ctx.proposed, ctx.container, ctx.others)

/** The dockable, floatable workspace that holds every panel. Memoized: it has no props, and
 *  its parent re-renders on every market tick. */
export const Workspace = memo(function Workspace() {
  const setApi = useContext(ApiSetterContext)
  const setCollapseForMenu = useContext(CollapseSetterContext)
  const apiRef = useRef<DockviewApi | null>(null)
  const [api, setLocalApi] = useState<DockviewApi | null>(null)
  const frame = useRef<HTMLDivElement>(null)
  const host = useRef<HTMLDivElement>(null)
  const saveTimer = useRef<number | undefined>(undefined)
  // The group being peeked at, if any: saved as collapsed.
  const peekRef = useRef<string | null>(null)
  const disposeSizes = useRef<() => void>(() => {})
  const disposeResizes = useRef<() => void>(() => {})
  const disposeTidy = useRef<() => void>(() => {})
  const disposeFromJSON = useRef<() => void>(() => {})

  const scheduleSave = (api: DockviewApi) => {
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = undefined
      saveLayout(snapshot(api, peekRef.current))
    }, SAVE_DELAY_MS)
  }
  const collapse = useEdgeStrips(api, frame, peekRef, () => {
    if (apiRef.current) scheduleSave(apiRef.current)
  })
  useEffect(() => setCollapseForMenu(collapse), [collapse, setCollapseForMenu])

  // Save right away when the page is closing: a delayed save would be lost, and dockview
  // reports layout changes a moment later, so there may not even be one pending yet.
  // When this effect is cleaned up, finish any pending save. (Don't drop the API here:
  // React's development mode runs this cleanup right after mounting, while the workspace
  // stays, and that broke saving.)
  useEffect(() => {
    const save = () => {
      window.clearTimeout(saveTimer.current)
      saveTimer.current = undefined
      try {
        if (apiRef.current) saveLayout(snapshot(apiRef.current, peekRef.current))
      } catch {
        // The workspace was already torn down; its last state was saved before.
      }
    }
    window.addEventListener('pagehide', save)
    return () => {
      window.removeEventListener('pagehide', save)
      if (saveTimer.current !== undefined) save()
    }
  }, [peekRef])

  const onReady = (event: DockviewReadyEvent) => {
    const api = event.api
    apiRef.current = api
    // dockview's fromJSON lays the grid out at a size of 0 when floating windows are being
    // replaced (Reset layout then built everything 0px wide, so nothing showed). After any
    // load or rebuild, lay it out at the workspace's real size, measured as dockview does.
    const relayout = () => {
      const el = host.current
      if (el?.clientWidth && el.clientHeight) api.layout(el.clientWidth, el.clientHeight, true)
    }
    disposeFromJSON.current()
    const afterLoad = api.onDidLayoutFromJSON(relayout)
    disposeFromJSON.current = () => afterLoad.dispose()
    let saved = loadLayout(new Set(Object.keys(COMPONENTS)))
    try {
      if (saved) api.fromJSON(saved)
      else buildDefaultLayout(api)
    } catch {
      // A saved layout from an older version, or corrupted: start from the default.
      saved = null
      clearLayout()
      buildDefaultLayout(api)
    }
    // Before the side sizes are restored, which are relative to the chart's place.
    keepChartSpace(api, relayout)
    if (saved) collapseEmptySideColumn(api)
    // StrictMode's development double-mount readies a second dockview on the same element.
    disposeSizes.current()
    if (host.current) disposeSizes.current = keepSidePanelSizes(api, host.current, saved)
    disposeResizes.current()
    disposeResizes.current = snapFloatingResizes(api)
    // Panels have no tab bar (the Panels menu controls them), so each is a group of its own.
    // Floating windows keep dockview's bar to drag them by, labelled with the panel's name.
    const tidy = () => {
      for (const g of api.groups) {
        if (!g.header.hidden) g.header.hidden = true
        labelFloatingWindow(g)
      }
    }
    tidy()
    const tidyColumn = sideColumnTidier(api)
    disposeTidy.current()
    const subs = [
      api.onDidAddGroup(tidy),
      api.onDidLayoutChange(() => {
        // After this change has settled, not inside it.
        queueMicrotask(() => keepChartSpace(api, relayout))
        tidy()
        tidyColumn()
      }),
    ]
    disposeTidy.current = () => subs.forEach((s) => s.dispose())
    // Save shortly after any change (moves, resizes, floats, closes).
    api.onDidLayoutChange(() => scheduleSave(api))
    setApi(api)
    setLocalApi(api)
  }

  return (
    <CollapseContext.Provider value={collapse}>
      <div className="workspace-frame" ref={frame}>
        {/* Only edges with something collapsed: a strip re-renders on every tick for its counts. */}
        {EDGES.filter((edge) => collapse.collapsed.some((g) => g.edge === edge)).map((edge) => (
          <EdgeStrip key={edge} edge={edge} />
        ))}
        <div className="dock-host" ref={host}>
          <DockviewReact
            theme={themeDark}
            components={COMPONENTS}
            onReady={onReady}
            // No tabs to drag: docking is the Panels menu's job (and nothing can end up
            // tabbed behind another panel). Floating windows still move by their bar.
            disableDnd
            floatingGroupBounds="boundedWithinViewport"
            transformFloatingGroupDrag={snapFloatingDrag}
          />
        </div>
      </div>
    </CollapseContext.Provider>
  )
})

/** The layout to save: a group the user is only peeking at is saved collapsed. */
function snapshot(api: DockviewApi, peeking: string | null) {
  return withHidden(api.toJSON(), new Set(peeking ? [peeking] : []), mainGroup(api)?.id)
}

type Size = { width: number; height: number }

/** dockview shares out any change in space among all the panels, so a side panel balloons
 *  when a neighbour is popped out, closed or collapsed, gives way when a collapsed one comes
 *  back, and grows with the window or when an edge strip comes or goes. Instead, put the
 *  other panels back to their width (beside the chart) or height (above or below it) from
 *  just before and let the chart's group take up the difference. (Only while the chart is
 *  docked.) Groups are tracked as objects, not ids: a new layout reuses ids. */
function keepSidePanelSizes(api: DockviewApi, host: HTMLElement, saved: SerializedDockview | null): () => void {
  const gridSizes = () =>
    new Map<DockviewGroupPanel, Size>(
      api.groups.filter((g) => g.api.location.type === 'grid' && g.api.isVisible).map((g) => [g, { width: g.width, height: g.height }]),
    )
  let before = gridSizes()
  const restore = (now: Map<DockviewGroupPanel, Size>) => {
    const chart = mainGroup(api)
    if (!chart) return
    const layout = api.toJSON()
    for (const [g] of now) {
      const size = before.get(g)
      if (g === chart || !size) continue
      const edge = edgeOf(layout, g.id, chart.id)
      g.api.setSize(edge === 'left' || edge === 'right' ? { width: size.width } : { height: size.height })
    }
  }
  const sub = api.onDidLayoutChange(() => {
    const now = gridSizes()
    // Those setSize calls raise another (batched) layout change, which records the result.
    if (now.size !== before.size || [...now.keys()].some((g) => !before.has(g))) restore(now)
    before = gridSizes()
  })
  // A resize of the workspace doesn't raise a layout change. Lay dockview out at the new
  // size right away (it would on its own a moment later) and put the side panels back.
  const observer = new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect
    if (!width || !height || (width === api.width && height === api.height)) return
    api.layout(width, height)
    restore(gridSizes())
    before = gridSizes()
  })
  observer.observe(host)
  // A saved layout is scaled to the workspace's size now; give the side panels back the sizes
  // they were saved with.
  const chart = mainGroup(api)
  if (saved && chart) {
    const sizes = savedSizes(saved)
    const layout = api.toJSON()
    for (const g of api.groups) {
      const size = sizes.get(g.id)
      if (g === chart || !size || g.api.location.type !== 'grid' || !g.api.isVisible) continue
      const edge = edgeOf(layout, g.id, chart.id)
      g.api.setSize(edge === 'left' || edge === 'right' ? { width: size.width } : { height: size.height })
    }
    before = gridSizes()
  }
  return () => {
    sub.dispose()
    observer.disconnect()
  }
}

/** Put the panel's name on its floating window's drag bar. */
function labelFloatingWindow(group: DockviewGroupPanel): void {
  if (group.api.location.type !== 'floating') return
  const bar = group.element.closest('.dv-resize-container')?.querySelector<HTMLElement>('.dv-floating-titlebar')
  const title = group.activePanel?.title ?? ''
  if (bar && bar.dataset.title !== title) bar.dataset.title = title
}
