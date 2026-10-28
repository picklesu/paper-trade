import type { AddPanelOptions, DockviewApi, DockviewGroupPanel, IDockviewPanel } from 'dockview-react'
import type { FunctionComponent } from 'react'
import { ActivityView, ChartView, OrdersView, PositionsView, TicketView, WatchlistView } from './PanelViews'

export type PanelId = 'chart' | 'watchlist' | 'ticket' | 'positions' | 'orders' | 'activity'

/** Every view, by id. Panel ids double as dockview component names. */
export const PANELS: Record<PanelId, { title: string; component: FunctionComponent }> = {
  chart: { title: 'Chart', component: ChartView },
  watchlist: { title: 'Watchlist', component: WatchlistView },
  ticket: { title: 'Order Entry', component: TicketView },
  positions: { title: 'Positions', component: PositionsView },
  orders: { title: 'Orders', component: OrdersView },
  activity: { title: 'Activity', component: ActivityView },
}

export const PANEL_IDS = Object.keys(PANELS) as PanelId[]

export const COMPONENTS = Object.fromEntries(PANEL_IDS.map((id) => [id, PANELS[id].component]))

/** The panels beside the chart, top to bottom in their column at the left. */
export const SIDE_PANELS: PanelId[] = ['watchlist', 'ticket', 'positions', 'orders', 'activity']

/** The side column's default width (the chart takes the rest). */
export const SIDE_WIDTH = 300

// Panels have no tab bar, so each one is a group of its own (`group.header.hidden` is set on
// every group by the workspace), and the Panels menu places them.

/** The default workspace: the chart, with every other panel collapsed to the left edge.
 *  They're docked in a column left of the chart, each hidden; showing one or several
 *  stacks them in that column. */
export function buildDefaultLayout(api: DockviewApi): void {
  api.clear()
  api.addPanel({ id: 'chart', component: 'chart', title: PANELS.chart.title })
  let above: PanelId | null = null
  for (const id of SIDE_PANELS) {
    const position: AddPanelOptions['position'] = above ? { referencePanel: above, direction: 'below' } : { referencePanel: 'chart', direction: 'left' }
    const panel = api.addPanel({ id, component: id, title: PANELS[id].title, position, ...(above ? {} : { initialWidth: SIDE_WIDTH }) })
    panel.group.api.setVisible(false)
    above = id
  }
  api.getPanel('chart')?.api.setActive()
}

type Side = 'left' | 'right' | 'above' | 'below'

/** Where a panel belongs in the docked layout: beside a docked panel, or at an edge of the
 *  workspace. Floating panels never count as a reference, so a panel is never put inside
 *  a floating window by accident. */
export interface DockSpot {
  /** A docked panel to place it relative to; none means an edge of the whole workspace. */
  beside?: string
  side: Side
  /** Its default width (left/right). */
  size?: number
}

export function dockSpot(api: DockviewApi, id: PanelId): DockSpot {
  const docked = (p: PanelId) => p !== id && api.getPanel(p)?.group.api.location.type === 'grid'
  // The chart: at the right of the side column (or alone).
  if (id === 'chart') return { side: 'right' }
  // In the side column, in its usual order: below the panel before it, or above the one after.
  const order = SIDE_PANELS.indexOf(id)
  const before = SIDE_PANELS.slice(0, order).reverse().find(docked)
  if (before) return { beside: before, side: 'below' }
  const after = SIDE_PANELS.slice(order + 1).find(docked)
  if (after) return { beside: after, side: 'above' }
  return docked('chart') ? { beside: 'chart', side: 'left', size: SIDE_WIDTH } : { side: 'left', size: SIDE_WIDTH }
}

/** When the last shown panel leaves the side column (floated or closed) while others are
 *  collapsed in it, dockview keeps the column at its width, empty. Hiding a panel is what
 *  makes it give the space back, so show one collapsed panel and hide it again. */
export function tidySideColumn(api: DockviewApi): void {
  const docked = SIDE_PANELS.map((p) => api.getPanel(p)?.group).filter((g): g is DockviewGroupPanel => !!g && g.api.location.type === 'grid')
  const chart = api.getPanel('chart')?.group
  if (!docked.length || docked.some((g) => g.api.isVisible) || !chart || chart.api.location.type !== 'grid' || chart.width >= api.width - 1) return
  docked[0].api.setVisible(true)
  docked[0].api.setVisible(false)
}

/** Add a closed panel at its usual spot. */
function addPanel(api: DockviewApi, id: PanelId): IDockviewPanel {
  const spot = dockSpot(api, id)
  const position: AddPanelOptions['position'] = spot.beside ? { referencePanel: spot.beside, direction: spot.side } : { direction: spot.side }
  const size = spot.size === undefined ? {} : { initialWidth: spot.size }
  return api.addPanel({ id, component: id, title: PANELS[id].title, position, ...size })
}

/** Expand a group collapsed to an edge strip. A side column that was entirely collapsed
 *  comes back at its minimum width, so give it the usual width; a panel joining others in
 *  the column takes an even share of its height (the others shrink to make room). */
export function showGroup(api: DockviewApi, group: DockviewGroupPanel): void {
  if (group.api.location.type !== 'grid' || group.api.isVisible) return
  group.api.setVisible(true)
  if (!SIDE_PANELS.includes(group.panels[0]?.id as PanelId)) return
  if (group.width < SIDE_WIDTH * 0.75) group.api.setSize({ width: SIDE_WIDTH })
  const stacked = SIDE_PANELS.map((p) => api.getPanel(p)?.group).filter((g) => g && g.api.location.type === 'grid' && g.api.isVisible).length
  if (stacked > 1) group.api.setSize({ height: Math.round(api.height / stacked) })
}

/** Dock a panel and show it: bring it back if it's closed, out of its floating window, or
 *  out of the edge strip. */
export function dockPanel(api: DockviewApi, id: PanelId): IDockviewPanel {
  const panel = api.getPanel(id) ?? addPanel(api, id)
  if (panel.group.api.location.type !== 'grid') dockGroup(api, panel.group)
  showGroup(api, panel.group)
  return panel
}

/** Dock a floating group back at the usual spot of its (first) panel. */
export function dockGroup(api: DockviewApi, group: DockviewGroupPanel): void {
  const id = group.panels[0]?.id as PanelId | undefined
  if (!id) return
  const spot = dockSpot(api, id)
  const toPosition = { left: 'left', right: 'right', above: 'top', below: 'bottom' } as const
  const target = spot.beside ? api.getPanel(spot.beside)?.group : undefined
  group.api.moveTo({ group: target, position: toPosition[spot.side] })
  if (spot.size !== undefined) group.api.setSize({ width: spot.size })
  if (id === 'chart') {
    // Docking splits the neighbour's space evenly; the chart is the main view, so the side
    // column goes back to its usual width and the chart takes the rest.
    const side = SIDE_PANELS.map((p) => api.getPanel(p)?.group).find((g) => g && g !== group && g.api.location.type === 'grid')
    side?.api.setSize({ width: SIDE_WIDTH })
  }
}

const FLOAT_SIZE = { width: 340, height: 440 }
const FLOAT_MIN = { width: 220, height: 180 }
const CASCADE = 32

/** Pop a docked group out as a floating window, cascading from the top-right so a new
 *  window never hides one that's already floating. */
export function floatGroup(api: DockviewApi, group: DockviewGroupPanel): void {
  showGroup(api, group)
  const width = Math.max(FLOAT_MIN.width, Math.min(FLOAT_SIZE.width, api.width - 48))
  const height = Math.max(FLOAT_MIN.height, Math.min(FLOAT_SIZE.height, api.height - 48))
  const n = api.groups.filter((g) => g.api.location.type === 'floating').length % 8
  // Clear of the chart's top row and price scale.
  const x = Math.max(0, api.width - width - 72 - n * CASCADE)
  const y = Math.min(44 + n * CASCADE, Math.max(0, api.height - height))
  api.addFloatingGroup(group, { x, y, width, height })
}

/** How a panel is shown: docked and open, collapsed to its edge strip (or only peeked at),
 *  in a floating window, or closed. */
export type PanelState = 'open' | 'collapsed' | 'floating' | 'off'

export function panelState(api: DockviewApi, id: PanelId, peeking: string | null): PanelState {
  const panel = api.getPanel(id)
  if (!panel) return 'off'
  const group = panel.group
  if (group.api.location.type !== 'grid') return 'floating'
  return !group.api.isVisible || group.id === peeking ? 'collapsed' : 'open'
}
