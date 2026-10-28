import { type AddPanelOptions, type DockviewApi, type DockviewGroupPanel, type IDockviewPanel, Orientation } from 'dockview-react'
import type { FunctionComponent } from 'react'
import { type Edge, edgeOf } from './edges'
import { ChartView, OrdersView, PositionsView, TicketView, WatchlistView } from './PanelViews'

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

export const COMPONENTS = Object.fromEntries(PANEL_IDS.map((id) => [id, PANELS[id].component]))

/** The panels around the chart, in the order they line up on an edge (top to bottom on the
 *  left and right, left to right on the top and bottom). The chart is always docked in the
 *  middle. */
export const SIDE_PANELS: PanelId[] = ['watchlist', 'ticket', 'positions', 'orders']

/** Where each panel goes by default (and when brought back without a remembered edge). */
export const DEFAULT_EDGE: Partial<Record<PanelId, Edge>> = { watchlist: 'left', ticket: 'left', positions: 'bottom', orders: 'bottom' }

/** How thick an edge's panels are by default: the width of the left and right columns, the
 *  height of the top and bottom rows (the chart takes the rest). */
export const EDGE_SIZE: Record<Edge, number> = { left: 300, right: 300, top: 200, bottom: 220 }

const isColumn = (edge: Edge) => edge === 'left' || edge === 'right'
/** The dimension across an edge (a column's width, a row's height), and the one along it. */
const across = (edge: Edge) => (isColumn(edge) ? 'width' : 'height')
const along = (edge: Edge) => (isColumn(edge) ? 'height' : 'width')

// Panels have no tab bar, so each one is a group of its own (`group.header.hidden` is set on
// every group by the workspace), and the Panels menu places them.

const docked = (api: DockviewApi, id: PanelId) => {
  const group = api.getPanel(id)?.group
  return group?.api.location.type === 'grid' ? group : undefined
}

/** The edge each docked side panel is on, read from where it sits in the grid. */
export function panelEdges(api: DockviewApi): Map<PanelId, Edge> {
  const layout = api.toJSON()
  const chart = docked(api, 'chart')?.id
  const out = new Map<PanelId, Edge>()
  for (const id of SIDE_PANELS) {
    const group = docked(api, id)
    if (group) out.set(id, edgeOf(layout, group.id, chart))
  }
  return out
}

const groupsOn = (api: DockviewApi, edge: Edge, edges = panelEdges(api)) =>
  SIDE_PANELS.filter((id) => edges.get(id) === edge).map((id) => docked(api, id)!)

/** The default workspace: the chart, with Watchlist and Order Entry collapsed on the left
 *  and Positions and Orders on the bottom. */
export function buildDefaultLayout(api: DockviewApi): void {
  // Start from an empty grid, not `api.clear()`: that keeps the old grid's root branch.
  api.fromJSON({ grid: { root: { type: 'branch', data: [] }, orientation: Orientation.HORIZONTAL, width: api.width, height: api.height }, panels: {} })
  api.addPanel({ id: 'chart', component: 'chart', title: PANELS.chart.title })
  for (const id of SIDE_PANELS) addPanel(api, id, DEFAULT_EDGE[id]!).group.api.setVisible(false)
  api.getPanel('chart')?.api.setActive()
}

type Side = 'left' | 'right' | 'above' | 'below'

/** Where a panel goes on an edge: beside another panel on that edge (in `SIDE_PANELS`
 *  order), or, the first there, at the left or right of the workspace or above or below the
 *  chart. (Rows sit above and below the chart, between the columns.) */
export interface DockSpot {
  /** A docked panel to place it relative to; none means an edge of the whole workspace. */
  beside?: string
  side: Side
  /** Its default thickness across the edge, if it starts the edge. */
  size?: number
}

export function dockSpot(api: DockviewApi, id: PanelId, edge: Edge): DockSpot {
  const edges = panelEdges(api)
  const on = SIDE_PANELS.filter((p) => p !== id && edges.get(p) === edge)
  const order = SIDE_PANELS.indexOf(id)
  const before = SIDE_PANELS.slice(0, order).reverse().find((p) => on.includes(p))
  if (before) return { beside: before, side: isColumn(edge) ? 'below' : 'right' }
  const after = SIDE_PANELS.slice(order + 1).find((p) => on.includes(p))
  if (after) return { beside: after, side: isColumn(edge) ? 'above' : 'left' }
  const size = EDGE_SIZE[edge]
  if (edge === 'left' || edge === 'right') return { side: edge, size }
  return { beside: 'chart', side: edge === 'top' ? 'above' : 'below', size }
}

/** Add a closed panel on an edge. */
function addPanel(api: DockviewApi, id: PanelId, edge: Edge): IDockviewPanel {
  const spot = dockSpot(api, id, edge)
  const position: AddPanelOptions['position'] = spot.beside ? { referencePanel: spot.beside, direction: spot.side } : { direction: spot.side }
  const size = spot.size === undefined ? {} : isColumn(edge) ? { initialWidth: spot.size } : { initialHeight: spot.size }
  return api.addPanel({ id, component: id, title: PANELS[id].title, position, ...size })
}

/** Below this, a panel came back squeezed to dockview's minimum, not sized by the user. */
const SQUEEZED = 120

/** Expand a group collapsed to an edge strip. An edge that was entirely collapsed comes back
 *  at its minimum thickness, so give it the usual one; and if a panel on the edge ends up
 *  squeezed to its minimum length, share the edge evenly. Sizes the user chose are otherwise
 *  left alone. */
export function showGroup(api: DockviewApi, group: DockviewGroupPanel): void {
  if (group.api.location.type !== 'grid' || group.api.isVisible) return
  group.api.setVisible(true)
  const id = group.panels[0]?.id as PanelId | undefined
  const edge = id && panelEdges(api).get(id)
  if (!edge) return
  if (group[across(edge)] < SQUEEZED) group.api.setSize({ [across(edge)]: EDGE_SIZE[edge] })
  const shown = groupsOn(api, edge).filter((g) => g.api.isVisible)
  const dim = along(edge)
  if (shown.length > 1 && shown.some((g) => g[dim] < SQUEEZED)) {
    const share = Math.round(shown.reduce((sum, g) => sum + g[dim], 0) / shown.length)
    for (const g of shown) g.api.setSize({ [dim]: share })
  }
}

/** Give an edge whose panels are all collapsed its space back. dockview keeps the space of a
 *  branch whose last shown panel left (closed or moved away) while others are hidden in it;
 *  hiding is what gives it back, so show one and hide it again (its own size is kept). */
function collapseEdge(groups: DockviewGroupPanel[]): void {
  if (!groups.length || groups.some((g) => g.api.isVisible)) return
  groups[0].api.setVisible(true)
  groups[0].api.setVisible(false)
}

/** After loading a saved layout: an edge whose only shown panel was dropped (a panel that no
 *  longer exists) keeps its space, empty. */
export function collapseEmptyEdges(api: DockviewApi): void {
  const edges = panelEdges(api)
  for (const edge of ['left', 'right', 'top', 'bottom'] as const) collapseEdge(groupsOn(api, edge, edges))
}

/** The check to run on each layout change: an edge that goes from showing a panel to showing
 *  none gets its space back. It acts only on that change, so its own visibility changes
 *  can't set it off again. */
export function edgeTidier(api: DockviewApi): () => void {
  const showingEdges = (edges = panelEdges(api)) => {
    return new Set((['left', 'right', 'top', 'bottom'] as const).filter((e) => groupsOn(api, e, edges).some((g) => g.api.isVisible)))
  }
  let showing = showingEdges()
  return () => {
    const was = showing
    const edges = panelEdges(api)
    showing = showingEdges(edges)
    for (const edge of was) if (!showing.has(edge)) collapseEdge(groupsOn(api, edge, edges))
  }
}

/** How a panel is shown: open on its edge, collapsed to its edge strip, or closed; and which
 *  edge it's on (none when closed). */
export type PanelState = 'open' | 'collapsed' | 'off'

export interface PanelPlace {
  state: PanelState
  edge: Edge | null
}

export function panelPlaces(api: DockviewApi): Record<PanelId, PanelPlace> {
  const edges = panelEdges(api)
  return Object.fromEntries(
    PANEL_IDS.map((id) => {
      const group = docked(api, id)
      const state: PanelState = !group ? 'off' : group.api.isVisible ? 'open' : 'collapsed'
      return [id, { state, edge: edges.get(id) ?? null }]
    }),
  ) as Record<PanelId, PanelPlace>
}

/** Put a panel on an edge, open or collapsed: bring it back if it's closed, and move it if
 *  it's on another edge. An open panel moves as it is (keeping what's typed into it); a
 *  collapsed one is re-added there, as dockview can't move a hidden group reliably.
 *  Returns its group, collapsed or shown as asked. */
export function placePanel(api: DockviewApi, id: PanelId, edge: Edge, state: 'open' | 'collapsed'): DockviewGroupPanel {
  let panel = api.getPanel(id)
  if (panel && panelEdges(api).get(id) !== edge) {
    if (panel.group.api.isVisible) {
      const spot = dockSpot(api, id, edge)
      const toPosition = { left: 'left', right: 'right', above: 'top', below: 'bottom' } as const
      const target = spot.beside ? api.getPanel(spot.beside)?.group : undefined
      panel.group.api.moveTo({ group: target, position: toPosition[spot.side] })
    } else {
      panel.api.close()
      panel = undefined
    }
  }
  if (!panel) {
    panel = addPanel(api, id, edge)
    if (state === 'collapsed') panel.group.api.setVisible(false)
    return panel.group
  }
  if (state === 'open') showGroup(api, panel.group)
  else if (panel.group.api.isVisible) panel.group.api.setVisible(false)
  return panel.group
}
