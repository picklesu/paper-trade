import {
  type DockviewApi,
  type DockviewReadyEvent,
  DockviewReact,
  type FloatingGroupDragContext,
  type IDockviewHeaderActionsProps,
  themeDark,
} from 'dockview-react'
import 'dockview-react/dist/styles/dockview.css'
import { type ReactNode, useContext, useEffect, useRef, useState } from 'react'
import { useAppData } from './appDataContext'
import { ApiSetterContext, type LayoutControls, LayoutContext } from './layoutContext'
import { clearLayout, loadLayout, saveLayout } from './layoutStorage'
import { buildDefaultLayout, COMPONENTS, PANEL_IDS, type PanelId, reopenPanel } from './panels'
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

  // Tab titles carry live counts, like a broker's "Orders (2 working)".
  const { tick, orders } = useAppData()
  const positions = tick?.account.positions.length ?? 0
  const working = orders.filter((o) => o.status === 'working').length
  useEffect(() => {
    if (!api) return
    api.getPanel('positions')?.api.setTitle(positions ? `Positions (${positions})` : 'Positions')
    api.getPanel('orders')?.api.setTitle(working ? `Orders (${working} working)` : 'Orders')
  }, [api, open, positions, working])

  const controls: LayoutControls = {
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
  }
  return (
    <LayoutContext.Provider value={controls}>
      <ApiSetterContext.Provider value={setApi}>{children}</ApiSetterContext.Provider>
    </LayoutContext.Provider>
  )
}

const FLOAT_SIZE = { width: 340, height: 440 }
const SAVE_DELAY_MS = 300

/** The dockable, floatable workspace that holds every panel. */
export function Workspace() {
  const setApi = useContext(ApiSetterContext)
  const saveTimer = useRef<number | undefined>(undefined)

  const onReady = (event: DockviewReadyEvent) => {
    const api = event.api
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
      saveTimer.current = window.setTimeout(() => saveLayout(api.toJSON()), SAVE_DELAY_MS)
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
 *  back to their sizes from just before and let the chart's group take up the slack. */
function keepSidePanelSizes(api: DockviewApi): void {
  const gridSizes = () =>
    new Map<string, Size>(api.groups.filter((g) => g.api.location.type === 'grid').map((g) => [g.id, { width: g.width, height: g.height }]))
  let before = gridSizes()
  let correcting = false
  api.onDidLayoutChange(() => {
    if (correcting) return
    const now = gridSizes()
    const lost = [...before.keys()].some((id) => !now.has(id))
    if (lost) {
      correcting = true
      for (const g of api.groups) {
        const size = before.get(g.id)
        if (g.api.location.type !== 'grid' || !size || g.panels.some((p) => p.id === 'chart')) continue
        g.api.setSize(size)
      }
      correcting = false
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
      // Back into the grid, on the right (drag the tab elsewhere to choose another spot).
      group.api.moveTo({ position: 'right' })
    } else {
      const box = containerApi.width
      containerApi.addFloatingGroup(group, {
        x: Math.max(0, box - FLOAT_SIZE.width - 24),
        y: 24,
        width: FLOAT_SIZE.width,
        height: Math.min(FLOAT_SIZE.height, containerApi.height - 48),
      })
    }
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
        title={floating ? 'Dock back into the layout' : 'Pop out as a floating window'}
        aria-label={floating ? 'Dock panel' : 'Float panel'}
        onClick={toggleFloat}
      >
        {floating ? '⇲' : '⧉'}
      </button>
    </div>
  )
}
