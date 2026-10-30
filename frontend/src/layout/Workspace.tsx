import {
  type DockviewApi,
  type DockviewReadyEvent,
  DockviewReact,
  type SerializedDockview,
  themeDark,
} from 'dockview-react'
import 'dockview-react/dist/styles/dockview.css'
import { memo, type ReactNode, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { StripContext } from './stripContext'
import { EdgeStrip } from './EdgeStrip'
import { type Edge, edgeOf, savedSizes } from './edges'
import { ApiSetterContext, type LayoutControls, LayoutContext } from './layoutContext'
import { clearLayout, loadLayout, panelsAddedSinceSave, saveLayout } from './layoutStorage'
import { addCollapsed, buildDefaultLayout, collapseEmptyEdges, COMPONENTS, DEFAULT_EDGE, EDGE_SIZE, edgeTidier, PANEL_IDS, type PanelId, type PanelPlace, panelEdges, panelPlaces, placePanel, SIDE_PANELS } from './panels'
import { useEdgeStrips } from './useEdgeStrips'

const NOWHERE = Object.fromEntries(PANEL_IDS.map((id) => [id, { state: 'off', edge: null }])) as Record<PanelId, PanelPlace>
const samePlaces = (a: Record<PanelId, PanelPlace>, b: Record<PanelId, PanelPlace>) =>
  PANEL_IDS.every((id) => a[id].state === b[id].state && a[id].edge === b[id].edge)

/** Holds the dockview API so the Panels menu, outside the workspace, can drive each panel. */
export function LayoutProvider({ children }: { children: ReactNode }) {
  const [api, setApi] = useState<DockviewApi | null>(null)
  // An off panel keeps the edge it was on (or its default), to come back there.
  const [places, setPlaces] = useState<Record<PanelId, PanelPlace>>(NOWHERE)

  useEffect(() => {
    if (!api) return
    // Only when a place changes: layout changes come in on every divider drag.
    const sync = () => {
      setPlaces((current) => {
        const found = panelPlaces(api)
        const next = Object.fromEntries(
          PANEL_IDS.map((id) => [id, found[id].edge ? found[id] : { state: 'off', edge: current[id].edge ?? DEFAULT_EDGE[id] ?? null }]),
        ) as Record<PanelId, PanelPlace>
        return samePlaces(current, next) ? current : next
      })
    }
    sync()
    const subs = [api.onDidAddPanel(sync), api.onDidRemovePanel(sync), api.onDidLayoutChange(sync), api.onDidLayoutFromJSON(sync)]
    return () => subs.forEach((s) => s.dispose())
  }, [api])

  const controls = useMemo<LayoutControls>(() => {
    const place = (id: PanelId, edge: NonNullable<PanelPlace['edge']>, state: 'open' | 'collapsed') => {
      if (api) placePanel(api, id, edge, state)
    }
    const edgeFor = (id: PanelId) => places[id].edge ?? DEFAULT_EDGE[id] ?? 'left'
    return {
      places,
      setOn: (id, on) => {
        if (!api) return
        if (!on) api.getPanel(id)?.api.close()
        // Turned on, it opens on its edge (its tab then collapses it).
        else if (!api.getPanel(id)) place(id, edgeFor(id), 'open')
      },
      setEdge: (id, edge) => {
        if (!api) return
        // An off panel only remembers where to come back.
        if (!api.getPanel(id)) {
          setPlaces((current) => ({ ...current, [id]: { state: 'off', edge } }))
          return
        }
        // Read from the panel: the places above update a moment after a tab is clicked.
        const group = api.getPanel(id)?.group
        place(id, edge, group?.api.isVisible ? 'open' : 'collapsed')
      },
      reset: () => {
        if (!api) return
        clearLayout()
        buildDefaultLayout(api)
      },
    }
  }, [api, places])

  return (
    <LayoutContext.Provider value={controls}>
      <ApiSetterContext.Provider value={setApi}>
        {children}
      </ApiSetterContext.Provider>
    </LayoutContext.Provider>
  )
}

const SAVE_DELAY_MS = 300
const EDGES: Edge[] = ['top', 'left', 'right', 'bottom']

/** The workspace: the chart in the middle, the other panels docked on its edges. Memoized:
 *  it has no props, and its parent re-renders on every market tick. Keep the props given to
 *  DockviewReact stable too: it passes any changed option to `updateOptions`, which lays the
 *  whole grid out again from the sizes saved before a divider drag, so a panel being resized
 *  snapped back to where it started. */
export const Workspace = memo(function Workspace() {
  const setApi = useContext(ApiSetterContext)
  const apiRef = useRef<DockviewApi | null>(null)
  const [api, setLocalApi] = useState<DockviewApi | null>(null)
  const host = useRef<HTMLDivElement>(null)
  const saveTimer = useRef<number | undefined>(undefined)
  const disposeSizes = useRef<() => void>(() => {})
  const disposeTidy = useRef<() => void>(() => {})
  const disposeFromJSON = useRef<() => void>(() => {})

  const scheduleSave = (api: DockviewApi) => {
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = undefined
      saveLayout(api.toJSON(), PANEL_IDS)
    }, SAVE_DELAY_MS)
  }
  const strips = useEdgeStrips(api)

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
        if (apiRef.current) saveLayout(apiRef.current.toJSON(), PANEL_IDS)
      } catch {
        // The workspace was already torn down; its last state was saved before.
      }
    }
    window.addEventListener('pagehide', save)
    return () => {
      window.removeEventListener('pagehide', save)
      if (saveTimer.current !== undefined) save()
    }
  }, [])

  const onReady = (event: DockviewReadyEvent) => {
    const api = event.api
    apiRef.current = api
    // dockview's fromJSON can lay the grid out at a size of 0 (with floating windows, before
    // they were removed, Reset built everything 0px wide). After any load or rebuild, lay it
    // out at the workspace's real size, measured as dockview does.
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
    if (saved) {
      // Panels the app gained since the layout was saved come in collapsed. A problem
      // there only skips that panel (the Panels menu can still turn it on), never
      // the user's layout.
      for (const id of panelsAddedSinceSave(SIDE_PANELS)) {
        try {
          addCollapsed(api, id as PanelId)
        } catch {
          // Left off.
        }
      }
      collapseEmptyEdges(api)
    }
    // StrictMode's development double-mount readies a second dockview on the same element.
    disposeSizes.current()
    if (host.current) disposeSizes.current = keepSidePanelSizes(api, host.current, saved)
    // Panels have no tab bar (the Panels menu controls them), so each is a group of its own.
    const tidy = () => {
      for (const g of api.groups) if (!g.header.hidden) g.header.hidden = true
    }
    tidy()
    const tidyEdges = edgeTidier(api)
    disposeTidy.current()
    const subs = [
      api.onDidAddGroup(tidy),
      api.onDidLayoutChange(() => {
        tidy()
        tidyEdges()
      }),
    ]
    disposeTidy.current = () => subs.forEach((s) => s.dispose())
    // Save shortly after any change (moves, resizes, collapses, closes).
    api.onDidLayoutChange(() => scheduleSave(api))
    setApi(api)
    setLocalApi(api)
  }

  return (
    <StripContext.Provider value={strips}>
      <div className="workspace-frame">
        {/* Only edges with panels: a strip re-renders on every tick for its counts. */}
        {EDGES.filter((edge) => strips.tabs.some((t) => t.edge === edge)).map((edge) => (
          <EdgeStrip key={edge} edge={edge} />
        ))}
        <div className="dock-host" ref={host}>
          <DockviewReact
            theme={themeDark}
            components={COMPONENTS}
            onReady={onReady}
            // No tabs to drag and no floating windows: placing panels is the Panels menu's
            // job (and nothing can end up tabbed behind another panel).
            disableDnd
            disableFloatingGroups
          />
        </div>
      </div>
    </StripContext.Provider>
  )
})

/** dockview shares out any change in space among all the panels, so a side panel balloons
 *  when a neighbour is closed, collapsed or moved to another edge, gives way when one comes
 *  back, and grows with the window or when an edge strip comes or goes. Instead, after any
 *  such change, put each open side panel back to its thickness across its edge (a column's
 *  width, a row's height) from just before, or that edge's default if it's new there, and
 *  let the chart take up the difference. Tracked by panel: moving a panel to another edge
 *  replaces its group. */
function keepSidePanelSizes(api: DockviewApi, host: HTMLElement, saved: SerializedDockview | null): () => void {
  // Every docked group, shown or not: a change in this set is a structural change.
  const structure = () =>
    api.groups
      .filter((g) => g.api.location.type === 'grid')
      .map((g) => `${g.id}:${g.panels[0]?.id}:${g.api.isVisible}`)
      .sort()
      .join(',')
  // Each side panel's last thickness across its edge (kept while it's collapsed, so it opens
  // at the size it had), and which panels were open.
  type Sizes = { thickness: Map<PanelId, { edge: Edge; size: number }>; open: Set<PanelId> }
  const thicknessOf = (g: { width: number; height: number }, edge: Edge) => (edge === 'left' || edge === 'right' ? g.width : g.height)
  const record = (prev?: Sizes): Sizes => {
    const thickness = new Map(prev?.thickness)
    const open = new Set<PanelId>()
    for (const [id, edge] of panelEdges(api)) {
      const g = api.getPanel(id)!.group
      if (!g.api.isVisible) continue
      thickness.set(id, { edge, size: thicknessOf(g, edge) })
      open.add(id)
    }
    return { thickness, open }
  }
  let before = record()
  let shape = structure()
  /** Panels on one edge share its thickness: that of a panel already open there, else the
   *  panel's own last size on that edge, else the edge's default. */
  const restore = (from: Sizes) => {
    const edges = panelEdges(api)
    for (const edge of ['left', 'right', 'top', 'bottom'] as const) {
      const on = [...edges].filter(([, e]) => e === edge).map(([id]) => id)
      const known = (id: PanelId) => {
        const t = from.thickness.get(id)
        return t && t.edge === edge ? t.size : undefined
      }
      const shared = on.filter((id) => from.open.has(id)).map(known).find((v) => v !== undefined)
      for (const id of on) {
        const g = api.getPanel(id)!.group
        if (!g.api.isVisible) continue
        const size = shared ?? known(id) ?? EDGE_SIZE[edge]
        g.api.setSize(edge === 'left' || edge === 'right' ? { width: size } : { height: size })
      }
    }
  }
  const sub = api.onDidLayoutChange(() => {
    const now = structure()
    // Once dockview has finished this change (restoring inside it gets overridden by the rest
    // of the change). Those setSize calls raise another layout change, which records the result.
    if (now !== shape) {
      const from = before
      requestAnimationFrame(() => restore(from))
    }
    shape = now
    before = record(before)
  })
  // A resize of the workspace doesn't raise a layout change. Lay dockview out at the new
  // size right away (it would on its own a moment later) and put the side panels back.
  const observer = new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect
    if (!width || !height || (width === api.width && height === api.height)) return
    api.layout(width, height)
    restore(before)
    before = record(before)
  })
  observer.observe(host)
  // A saved layout is scaled to the workspace's size now; give the side panels back the sizes
  // they were saved with.
  const chart = chartGroup(api)
  if (saved && chart) {
    const sizes = savedSizes(saved)
    const layout = api.toJSON()
    for (const g of api.groups) {
      const size = sizes.get(g.id)
      if (g === chart || !size || g.api.location.type !== 'grid' || !g.api.isVisible) continue
      const edge = edgeOf(layout, g.id, chart.id)
      g.api.setSize(edge === 'left' || edge === 'right' ? { width: size.width } : { height: size.height })
    }
    before = record(before)
  }
  return () => {
    sub.dispose()
    observer.disconnect()
  }
}

const chartGroup = (api: DockviewApi) => api.getPanel('chart')?.group
