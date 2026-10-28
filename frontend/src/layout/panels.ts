import { type AddPanelOptions, type DockviewApi, type DockviewGroupPanel, type IDockviewPanel, Orientation } from 'dockview-react'
import type { FunctionComponent } from 'react'
import { ChartSpaceView, ChartView, OrdersView, PositionsView, TicketView, WatchlistView } from './PanelViews'

export type PanelId = 'chart' | 'watchlist' | 'ticket' | 'positions' | 'orders'

/** Every view, by id. Panel ids double as dockview component names. */
export const PANELS: Record<PanelId, { title: string; component: FunctionComponent }> = {
  chart: { title: 'Chart', component: ChartView },
  watchlist: { title: 'Watchlist', component: WatchlistView },
  ticket: { title: 'Order Entry', component: TicketView },
  positions: { title: 'Positions', component: PositionsView },
  orders: { title: 'Orders', component: OrdersView },
}

export const PANEL_IDS = Object.keys(PANELS) as PanelId[]

/** An empty panel that holds the chart's place in the grid while the chart floats. Without
 *  it the grid would be only the side column, which dockview stretches over the whole
 *  workspace (and its strips would move to the top and bottom). Not in the Panels menu. */
export const CHART_SPACE = 'chart-space'

export const COMPONENTS = { ...Object.fromEntries(PANEL_IDS.map((id) => [id, PANELS[id].component])), [CHART_SPACE]: ChartSpaceView }

/** The docked group in the chart's place: the chart's, or the placeholder's while it floats. */
export function mainGroup(api: DockviewApi): DockviewGroupPanel | undefined {
  const chart = api.getPanel('chart')?.group
  if (chart?.api.location.type === 'grid') return chart
  const space = api.getPanel(CHART_SPACE)?.group
  return space?.api.location.type === 'grid' ? space : undefined
}

/** Add the chart's placeholder: beside the docked chart (as it's about to float), or at the
 *  right of the grid. */
function addChartSpace(api: DockviewApi, besideChart: boolean): void {
  const position: AddPanelOptions['position'] = besideChart ? { referencePanel: 'chart', direction: 'right' } : { direction: 'right' }
  api.addPanel({ id: CHART_SPACE, component: CHART_SPACE, title: '', position })
}

/** Keep the placeholder exactly while the chart floats: on load and after layout changes.
 *  (`floatGroup` adds it as the chart leaves and `dockGroup` swaps the chart back in; this
 *  covers anything else, such as a layout saved before the placeholder existed.) Added to
 *  a grid of only collapsed panels, which dockview has shrunk to nothing, it starts at its
 *  minimum size, so `relayout` (the grid laid out at the workspace's size) follows. */
export function keepChartSpace(api: DockviewApi, relayout: () => void): void {
  const chart = api.getPanel('chart')
  if (!chart) return
  const docked = chart.group.api.location.type === 'grid'
  const space = api.getPanel(CHART_SPACE)
  if (docked && space) space.api.close()
  else if (!docked && !space) {
    addChartSpace(api, false)
    relayout()
    resetSideColumn(api)
  }
}

const dockedSide = (api: DockviewApi) =>
  SIDE_PANELS.map((p) => api.getPanel(p)?.group).filter((g): g is DockviewGroupPanel => !!g && g.api.location.type === 'grid')

/** Collapse a side column that shows nothing but still has width, to no width: show one of
 *  its panels at the usual width and hide it again, as only hiding gives the space back
 *  (and the panel then comes back at that width). */
function collapseSideColumn(docked: DockviewGroupPanel[]): void {
  if (!docked.length) return
  docked[0].api.setVisible(true)
  docked[0].api.setSize({ width: SIDE_WIDTH })
  docked[0].api.setVisible(false)
}

/** Put the side column back to its usual width; if nothing in it is showing, collapsed to no
 *  width. For a grid rebuilt around the column, where it had any share of the width. */
function resetSideColumn(api: DockviewApi): void {
  const docked = dockedSide(api)
  const shown = docked.find((g) => g.api.isVisible)
  if (shown) shown.api.setSize({ width: SIDE_WIDTH })
  else collapseSideColumn(docked)
}

/** After loading a saved layout: a column whose only shown panel was dropped (Activity, when
 *  it was removed) keeps its width, empty. */
export function collapseEmptySideColumn(api: DockviewApi): void {
  const docked = dockedSide(api)
  const main = mainGroup(api)
  if (docked.some((g) => g.api.isVisible) || !main || main.width >= api.width - 1) return
  collapseSideColumn(docked)
}

/** The panels beside the chart, top to bottom in their column at the left. */
export const SIDE_PANELS: PanelId[] = ['watchlist', 'ticket', 'positions', 'orders']

/** The side column's default width (the chart takes the rest). */
export const SIDE_WIDTH = 300

// Panels have no tab bar, so each one is a group of its own (`group.header.hidden` is set on
// every group by the workspace), and the Panels menu places them.

/** The default workspace: the chart, with every other panel collapsed to the left edge.
 *  They're docked in a column left of the chart, each hidden; showing one or several
 *  stacks them in that column. */
export function buildDefaultLayout(api: DockviewApi): void {
  // Start from an empty grid, not `api.clear()`: that keeps the old grid's root branch, and
  // after floating windows the panels below were wrapped in an extra branch 0px tall, so
  // Reset layout showed nothing.
  api.fromJSON({ grid: { root: { type: 'branch', data: [] }, orientation: Orientation.HORIZONTAL, width: api.width, height: api.height }, panels: {} })
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
  if (docked('chart')) return { beside: 'chart', side: 'left', size: SIDE_WIDTH }
  if (api.getPanel(CHART_SPACE)) return { beside: CHART_SPACE, side: 'left', size: SIDE_WIDTH }
  return { side: 'left', size: SIDE_WIDTH }
}

/** When the last shown panel leaves the side column (floated or closed) while others are
 *  collapsed in it, dockview keeps the column at its width, empty. Hiding a panel is what
 *  makes it give the space back, so show one collapsed panel and hide it again. Returns the
 *  check to run on each layout change; it acts only as the column goes from showing a panel
 *  to showing none, so its own visibility changes can't set it off again. */
export function sideColumnTidier(api: DockviewApi): () => void {
  const docked = () => SIDE_PANELS.map((p) => api.getPanel(p)?.group).filter((g): g is DockviewGroupPanel => !!g && g.api.location.type === 'grid')
  let showing = docked().some((g) => g.api.isVisible)
  return () => {
    const groups = docked()
    const was = showing
    showing = groups.some((g) => g.api.isVisible)
    const main = mainGroup(api)
    if (!was || showing || !groups.length || !main || main.width >= api.width - 1) return
    groups[0].api.setVisible(true)
    groups[0].api.setVisible(false)
    showing = false
  }
}

/** Add a closed panel at its usual spot. */
function addPanel(api: DockviewApi, id: PanelId): IDockviewPanel {
  const spot = dockSpot(api, id)
  const position: AddPanelOptions['position'] = spot.beside ? { referencePanel: spot.beside, direction: spot.side } : { direction: spot.side }
  const size = spot.size === undefined ? {} : { initialWidth: spot.size }
  return api.addPanel({ id, component: id, title: PANELS[id].title, position, ...size })
}

/** Below this, a side panel came back squeezed to dockview's minimum, not sized by the user. */
const SQUEEZED = 120

/** Expand a group collapsed to an edge strip. A side column that was entirely collapsed
 *  comes back at its minimum width, so give it the usual width; and if a panel in the column
 *  ends up squeezed to its minimum height, share the height evenly. Sizes the user chose are
 *  otherwise left alone. */
export function showGroup(api: DockviewApi, group: DockviewGroupPanel): void {
  if (group.api.location.type !== 'grid' || group.api.isVisible) return
  group.api.setVisible(true)
  if (!SIDE_PANELS.includes(group.panels[0]?.id as PanelId)) return
  if (group.width < SQUEEZED) group.api.setSize({ width: SIDE_WIDTH })
  const stacked = SIDE_PANELS.map((p) => api.getPanel(p)?.group).filter((g): g is DockviewGroupPanel => !!g && g.api.location.type === 'grid' && g.api.isVisible)
  if (stacked.length > 1 && stacked.some((g) => g.height < SQUEEZED)) {
    const share = Math.round(api.height / stacked.length)
    for (const g of stacked) g.api.setSize({ height: share })
  }
}

/** Dock a panel: bring it back if it's closed, or out of its floating window; and unless
 *  it's about to be collapsed, show it (out of the edge strip). */
export function dockPanel(api: DockviewApi, id: PanelId, show = true): IDockviewPanel {
  const panel = api.getPanel(id) ?? addPanel(api, id)
  if (panel.group.api.location.type !== 'grid') dockGroup(api, panel.group)
  if (show) showGroup(api, panel.group)
  return panel
}

/** Dock a floating group back at the usual spot of its (first) panel. */
export function dockGroup(api: DockviewApi, group: DockviewGroupPanel): void {
  const id = group.panels[0]?.id as PanelId | undefined
  if (!id) return
  const space = api.getPanel(CHART_SPACE)
  if (id === 'chart' && space) {
    // Into the placeholder's place, which then goes.
    group.api.moveTo({ group: space.group, position: 'center' })
    space.api.close()
  } else {
    const spot = dockSpot(api, id)
    const toPosition = { left: 'left', right: 'right', above: 'top', below: 'bottom' } as const
    const target = spot.beside ? api.getPanel(spot.beside)?.group : undefined
    group.api.moveTo({ group: target, position: toPosition[spot.side] })
    if (spot.size !== undefined) group.api.setSize({ width: spot.size })
  }
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
  // A collapsed panel floats straight out of the column (showing it first would leave the
  // column open and empty once it's gone), then shows in its window.
  const hidden = !group.api.isVisible
  // The chart floats large enough to read; other panels at a panel's size.
  const isChart = group.panels.some((p) => p.id === 'chart')
  const size = isChart ? { width: Math.round(api.width * 0.6), height: Math.round(api.height * 0.7) } : FLOAT_SIZE
  const width = Math.max(FLOAT_MIN.width, Math.min(size.width, api.width - 48))
  const height = Math.max(FLOAT_MIN.height, Math.min(size.height, api.height - 48))
  const n = api.groups.filter((g) => g.api.location.type === 'floating').length % 8
  // Clear of the chart's top row and price scale.
  const x = Math.max(0, api.width - width - 72 - n * CASCADE)
  const y = Math.min(44 + n * CASCADE, Math.max(0, api.height - height))
  // The chart's placeholder goes in beside it before it leaves, so it simply takes over the
  // chart's space. (Added afterwards, it would start from a grid of only collapsed panels,
  // which dockview has shrunk to nothing.)
  if (isChart && !api.getPanel(CHART_SPACE)) addChartSpace(api, true)
  api.addFloatingGroup(group, { x, y, width, height })
  if (hidden || !group.api.isVisible) group.api.setVisible(true)
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
