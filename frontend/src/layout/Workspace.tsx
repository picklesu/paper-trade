import {
  type DockviewApi,
  type DockviewReadyEvent,
  DockviewReact,
  type FloatingGroupDragContext,
  type IDockviewHeaderActionsProps,
  themeDark,
} from 'dockview-react'
import 'dockview-react/dist/styles/dockview.css'
import { type ReactNode, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useAppData } from './appDataContext'
import { ApiSetterContext, type LayoutControls, LayoutContext } from './layoutContext'
import { clearLayout, loadLayout, saveLayout } from './layoutStorage'
import { buildDefaultLayout, COMPONENTS, dockGroup, PANEL_IDS, type PanelId, reopenPanel } from './panels'
import { snapPosition } from './snap'

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

/** The dockable, floatable workspace that holds every panel. */
export function Workspace() {
  const setApi = useContext(ApiSetterContext)
  const apiRef = useRef<DockviewApi | null>(null)
  const saveTimer = useRef<number | undefined>(undefined)

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
        if (apiRef.current) saveLayout(apiRef.current.toJSON())
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
    const saved = loadLayout()
    try {
      if (saved) api.fromJSON(saved)
      else buildDefaultLayout(api)
    } catch {
      // A saved layout from an older version, or corrupted: start from the default.
      clearLayout()
      buildDefaultLayout(api)
    }
    keepSidePanelSizes(api)
    // Save shortly after any change (moves, resizes, floats, closes).
    api.onDidLayoutChange(() => {
      window.clearTimeout(saveTimer.current)
      saveTimer.current = window.setTimeout(() => {
        saveTimer.current = undefined
        saveLayout(api.toJSON())
      }, SAVE_DELAY_MS)
    })
    setApi(api)
  }

  return (
    <DockviewReact
      theme={themeDark}
      components={COMPONENTS}
      rightHeaderActionsComponent={GroupActions}
      onReady={onReady}
      floatingGroupBounds="boundedWithinViewport"
      // Floating windows snap to the workspace edges and to each other.
      transformFloatingGroupDrag={(ctx: FloatingGroupDragContext) => snapPosition(ctx.proposed, ctx.container, ctx.others)}
    />
  )
}

type Size = { width: number; height: number }

/** When a panel leaves the grid (popped out or closed), the freed space would be shared
 *  among all remaining panels, so a side panel balloons. Instead, put the other panels
 *  back to their sizes from just before and let the chart's group take up the slack.
 *  (Only while the chart is docked, and never from a maximized layout, where the hidden
 *  groups report collapsed sizes.) */
function keepSidePanelSizes(api: DockviewApi): void {
  const gridSizes = () =>
    new Map<string, Size>(api.groups.filter((g) => g.api.location.type === 'grid').map((g) => [g.id, { width: g.width, height: g.height }]))
  let before = gridSizes()
  api.onDidLayoutChange(() => {
    if (api.hasMaximizedGroup()) return // keep the pre-maximize sizes as the reference
    const now = gridSizes()
    const lost = [...before.keys()].some((id) => !now.has(id))
    const chart = api.getPanel('chart')?.group
    if (lost && chart && chart.api.location.type === 'grid') {
      for (const g of api.groups) {
        const size = before.get(g.id)
        if (g !== chart && g.api.location.type === 'grid' && size) g.api.setSize(size)
      }
      // Those setSize calls raise another (batched) layout change, which records the result.
    }
    before = gridSizes()
  })
}

/** Buttons at the right of each group's tab bar: float or dock it, maximize or restore it. */
function GroupActions({ group, containerApi }: IDockviewHeaderActionsProps) {
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

  return (
    <div className="group-actions">
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
