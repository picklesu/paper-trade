import {
  type DockviewApi,
  type DockviewGroupPanel,
  type DockviewReadyEvent,
  DockviewReact,
  type FloatingGroupDragContext,
  type IDockviewHeaderActionsProps,
  type SerializedDockview,
  themeDark,
} from 'dockview-react'
import 'dockview-react/dist/styles/dockview.css'
import { type ReactNode, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useAppData } from './appDataContext'
import { type ChartToolbarSlot as ToolbarSlot, ChartToolbarSlotContext, SetChartToolbarSlotContext } from './chartToolbarSlot'
import { CollapseContext, useCollapse } from './collapseContext'
import { EdgeStrip } from './EdgeStrip'
import { type Edge, edgeOf, savedSizes, withHidden } from './edges'
import { ApiSetterContext, type LayoutControls, LayoutContext } from './layoutContext'
import { clearLayout, loadLayout, saveLayout } from './layoutStorage'
import { buildDefaultLayout, COMPONENTS, dockGroup, PANEL_IDS, type PanelId, reopenPanel } from './panels'
import { snapFloatingResizes } from './floatingResize'
import { snapPosition } from './snap'
import { useEdgeStrips } from './useEdgeStrips'

/** Holds the dockview API so the Panels menu (outside the workspace) can drive it. */
export function LayoutProvider({ children }: { children: ReactNode }) {
  const [api, setApi] = useState<DockviewApi | null>(null)
  const [open, setOpen] = useState<Set<PanelId>>(new Set())

  useEffect(() => {
    if (!api) return
    const sync = () => setOpen(new Set(PANEL_IDS.filter((id) => api.getPanel(id))))
    sync()
    const subs = [api.onDidAddPanel(sync), api.onDidRemovePanel(sync), api.onDidLayoutFromJSON(sync)]
    return () => subs.forEach((s) => s.dispose())
  }, [api])

  const controls = useMemo<LayoutControls>(
    () => ({
      open,
      toggle: (id) => {
        if (!api) return
        const panel = api.getPanel(id)
        if (panel) panel.api.close()
        else reopenPanel(api, id)
      },
      reset: () => {
        if (!api) return
        clearLayout()
        buildDefaultLayout(api)
      },
    }),
    [api, open],
  )

  return (
    <LayoutContext.Provider value={controls}>
      <ApiSetterContext.Provider value={setApi}>{children}</ApiSetterContext.Provider>
      {api && <LiveTabTitles api={api} open={open} />}
    </LayoutContext.Provider>
  )
}

/** Tab titles with live counts, like a broker's "Orders (2 working)". Its own component so
 *  only it re-renders on every market tick. */
function LiveTabTitles({ api, open }: { api: DockviewApi; open: Set<PanelId> }) {
  const { tick, orders } = useAppData()
  const positions = tick?.account.positions.length ?? 0
  const working = orders.filter((o) => o.status === 'working').length
  useEffect(() => {
    api.getPanel('positions')?.api.setTitle(positions ? `Positions (${positions})` : 'Positions')
    api.getPanel('orders')?.api.setTitle(working ? `Orders (${working} working)` : 'Orders')
  }, [api, open, positions, working])
  return null
}

const FLOAT_SIZE = { width: 340, height: 440 }
const FLOAT_MIN = { width: 220, height: 180 }
const CASCADE = 32
const SAVE_DELAY_MS = 300
const EDGES: Edge[] = ['top', 'left', 'right', 'bottom']
const COLLAPSE_ICON: Record<Edge, string> = { left: '⇤', right: '⇥', top: '⤒', bottom: '⤓' }

/** The dockable, floatable workspace that holds every panel. */
export function Workspace() {
  const setApi = useContext(ApiSetterContext)
  const apiRef = useRef<DockviewApi | null>(null)
  const [api, setLocalApi] = useState<DockviewApi | null>(null)
  const frame = useRef<HTMLDivElement>(null)
  const host = useRef<HTMLDivElement>(null)
  const saveTimer = useRef<number | undefined>(undefined)
  // The group being peeked at, if any: saved as collapsed.
  const peekRef = useRef<string | null>(null)
  const disposeSizes = useRef<() => void>(() => {})
  const disposeResizes = useRef<() => void>(() => {})
  const [toolbarSlot, setToolbarSlot] = useState<ToolbarSlot | null>(null)

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
    let saved = loadLayout()
    try {
      if (saved) api.fromJSON(saved)
      else buildDefaultLayout(api)
    } catch {
      // A saved layout from an older version, or corrupted: start from the default.
      saved = null
      clearLayout()
      buildDefaultLayout(api)
    }
    // StrictMode's development double-mount readies a second dockview on the same element.
    disposeSizes.current()
    if (host.current) disposeSizes.current = keepSidePanelSizes(api, host.current, saved)
    disposeResizes.current()
    disposeResizes.current = snapFloatingResizes(api)
    // Save shortly after any change (moves, resizes, floats, closes).
    api.onDidLayoutChange(() => scheduleSave(api))
    setApi(api)
    setLocalApi(api)
  }

  return (
    <CollapseContext.Provider value={collapse}>
      <ChartToolbarSlotContext.Provider value={toolbarSlot}>
        <SetChartToolbarSlotContext.Provider value={setToolbarSlot}>
          <div className="workspace-frame" ref={frame}>
            {EDGES.map((edge) => (
              <EdgeStrip key={edge} edge={edge} />
            ))}
            <div className="dock-host" ref={host}>
              <DockviewReact
                theme={themeDark}
                components={COMPONENTS}
                rightHeaderActionsComponent={GroupActions}
                leftHeaderActionsComponent={ChartToolbarSlot}
                onReady={onReady}
                floatingGroupBounds="boundedWithinViewport"
                // Floating windows snap to the workspace edges and to each other.
                transformFloatingGroupDrag={(ctx: FloatingGroupDragContext) => snapPosition(ctx.proposed, ctx.container, ctx.others)}
              />
            </div>
          </div>
        </SetChartToolbarSlotContext.Provider>
      </ChartToolbarSlotContext.Provider>
    </CollapseContext.Provider>
  )
}

/** The layout to save: a group the user is only peeking at is saved collapsed. */
function snapshot(api: DockviewApi, peeking: string | null) {
  const chart = api.getPanel('chart')?.group
  return withHidden(api.toJSON(), new Set(peeking ? [peeking] : []), chart?.api.location.type === 'grid' ? chart.id : undefined)
}

type Size = { width: number; height: number }

/** dockview shares out any change in space among all the panels, so a side panel balloons
 *  when a neighbour is popped out, closed or collapsed, gives way when a collapsed one comes
 *  back, and grows with the window or when an edge strip comes or goes. Instead, put the
 *  other panels back to their width (beside the chart) or height (above or below it) from
 *  just before and let the chart's group take up the difference. (Only while the chart is
 *  docked, and never from a maximized layout, where the hidden groups report collapsed
 *  sizes.) Groups are tracked as objects, not ids: a new layout reuses ids. */
function keepSidePanelSizes(api: DockviewApi, host: HTMLElement, saved: SerializedDockview | null): () => void {
  const gridSizes = () =>
    new Map<DockviewGroupPanel, Size>(
      api.groups.filter((g) => g.api.location.type === 'grid' && g.api.isVisible).map((g) => [g, { width: g.width, height: g.height }]),
    )
  let before = gridSizes()
  const restore = (now: Map<DockviewGroupPanel, Size>) => {
    const chart = api.getPanel('chart')?.group
    if (!chart || chart.api.location.type !== 'grid') return
    const layout = api.toJSON()
    for (const [g] of now) {
      const size = before.get(g)
      if (g === chart || !size) continue
      const edge = edgeOf(layout, g.id, chart.id)
      g.api.setSize(edge === 'left' || edge === 'right' ? { width: size.width } : { height: size.height })
    }
  }
  const sub = api.onDidLayoutChange(() => {
    if (api.hasMaximizedGroup()) return // keep the pre-maximize sizes as the reference
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
    if (api.hasMaximizedGroup()) return
    restore(gridSizes())
    before = gridSizes()
  })
  observer.observe(host)
  // A saved layout is scaled to the workspace's size now; give the side panels back the sizes
  // they were saved with.
  const chart = api.getPanel('chart')?.group
  if (saved && chart?.api.location.type === 'grid') {
    const sizes = savedSizes(saved)
    for (const g of api.groups) {
      const size = sizes.get(g.id)
      if (g !== chart && size && g.api.location.type === 'grid' && g.api.isVisible) g.api.setSize(size)
    }
    before = gridSizes()
  }
  return () => {
    sub.dispose()
    observer.disconnect()
  }
}

/** The chart toolbar's width with every button, and in its compact form (measured), plus a
 *  little room to grab the tab bar. */
const TOOLBAR_FULL_WIDTH = 660
const TOOLBAR_COMPACT_WIDTH = 320
const DRAG_ROOM = 40

/** Whether the chart's toolbar fits in its tab bar beside the tabs and the buttons at the
 *  right: with every button, compact, or not at all (it stays in the panel). Tab widths are
 *  estimated from their titles: measuring them would include the toolbar's own squeeze. */
function toolbarFit(group: DockviewGroupPanel): 'full' | 'compact' | null {
  if (group.activePanel?.id !== 'chart') return null
  const tabs = group.panels.reduce((w, p) => w + (p.title ?? p.id).length * 7 + 40, 0)
  const actions = group.element.querySelector<HTMLElement>('.dv-right-actions-container')?.offsetWidth ?? 0
  const room = group.width - tabs - actions - DRAG_ROOM
  return room >= TOOLBAR_FULL_WIDTH ? 'full' : room >= TOOLBAR_COMPACT_WIDTH ? 'compact' : null
}

/** In the chart's tab bar, after the tabs: the spot the chart's toolbar moves into while the
 *  chart is the group's visible panel and the group is wide enough. */
function ChartToolbarSlot({ group, containerApi }: IDockviewHeaderActionsProps) {
  const setSlot = useContext(SetChartToolbarSlotContext)
  const [fit, setFit] = useState(() => toolbarFit(group))
  const [element, setElement] = useState<HTMLElement | null>(null)
  useEffect(() => {
    const update = () => setFit(toolbarFit(group))
    update()
    const subs = [
      group.api.onDidActivePanelChange(update),
      group.api.onDidDimensionsChange(update),
      // Tabs joining or leaving the group change the room left.
      containerApi.onDidAddPanel(update),
      containerApi.onDidRemovePanel(update),
      containerApi.onDidMovePanel(update),
    ]
    return () => subs.forEach((s) => s.dispose())
  }, [group, containerApi])
  useEffect(() => {
    if (!element || !fit) return
    setSlot({ element, compact: fit === 'compact' })
    // Only clear our own slot: the chart may already have moved to another group's.
    return () => setSlot((current) => (current?.element === element ? null : current))
  }, [element, fit, setSlot])
  return fit ? <div className="chart-toolbar-slot" ref={setElement} /> : null
}

/** Buttons at the right of each group's tab bar: collapse it to an edge (or pin it open),
 *  float or dock it, maximize or restore it. */
function GroupActions({ group, containerApi }: IDockviewHeaderActionsProps) {
  const collapse = useCollapse()
  const [floating, setFloating] = useState(group.api.location.type === 'floating')
  const [maximized, setMaximized] = useState(group.api.isMaximized())

  useEffect(() => {
    const subs = [
      group.api.onDidLocationChange((e) => setFloating(e.location.type === 'floating')),
      containerApi.onDidMaximizedGroupChange(() => setMaximized(group.api.isMaximized())),
    ]
    return () => subs.forEach((s) => s.dispose())
  }, [group, containerApi])

  const toggleFloat = () => {
    if (floating) {
      // Back to where this panel usually lives.
      dockGroup(containerApi, group)
      return
    }
    const width = Math.max(FLOAT_MIN.width, Math.min(FLOAT_SIZE.width, containerApi.width - 48))
    const height = Math.max(FLOAT_MIN.height, Math.min(FLOAT_SIZE.height, containerApi.height - 48))
    // Cascade from the top-right, so a new window never hides one that's already floating.
    const n = containerApi.groups.filter((g) => g.api.location.type === 'floating').length % 8
    const x = Math.max(0, containerApi.width - width - 24 - n * CASCADE)
    const y = Math.min(24 + n * CASCADE, Math.max(0, containerApi.height - height))
    containerApi.addFloatingGroup(group, { x, y, width, height })
  }

  // The chart is the main view, so its group doesn't collapse.
  const edge = !floating && !maximized && !group.panels.some((p) => p.id === 'chart') ? collapse?.edges.get(group.id) : undefined
  const peeking = collapse?.peeking === group.id

  return (
    <div className="group-actions">
      {peeking && (
        <button className="group-action" title="Keep open" aria-label="Pin panel open" onClick={() => collapse?.pin()}>
          <PinIcon />
        </button>
      )}
      {edge && (
        <button
          className="group-action"
          title={`Collapse to the ${edge} edge`}
          aria-label="Collapse panel"
          onClick={() => collapse?.collapse(group)}
        >
          {COLLAPSE_ICON[edge]}
        </button>
      )}
      {!floating && (
        <button
          className="group-action"
          title={maximized ? 'Restore' : 'Maximize'}
          aria-label={maximized ? 'Restore panel size' : 'Maximize panel'}
          onClick={() => (maximized ? group.api.exitMaximized() : group.api.maximize())}
        >
          {maximized ? '⤡' : '⤢'}
        </button>
      )}
      <button
        className="group-action"
        title={floating ? 'Dock back to its usual place' : 'Pop out as a floating window'}
        aria-label={floating ? 'Dock panel' : 'Float panel'}
        onClick={toggleFloat}
      >
        {floating ? '⇲' : '⧉'}
      </button>
    </div>
  )
}

function PinIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <path d="M10.5 1.5 14.5 5.5 13 6l-2.5 2.5.5 3-1.5 1.5L7 10.5 3 14.5 1.5 13l4-4L3 6.5 4.5 5l3 .5L10 3z" />
    </svg>
  )
}
