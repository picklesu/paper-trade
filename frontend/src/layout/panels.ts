import type { AddPanelOptions, DockviewApi, DockviewGroupPanel } from 'dockview-react'
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

const ACCOUNT: PanelId[] = ['positions', 'orders', 'activity']

/** Default widths of the side panels and height of the account tabs (the chart takes the rest). */
const SIDE_WIDTH = { watchlist: 250, ticket: 300 } as const
const ACCOUNT_HEIGHT = 180

/** The default workspace: chart in the middle, watchlist left, order entry right,
 *  and the account views tabbed along the bottom. */
export function buildDefaultLayout(api: DockviewApi): void {
  api.clear()
  api.addPanel({ id: 'chart', component: 'chart', title: PANELS.chart.title })
  api.addPanel({ id: 'positions', component: 'positions', title: PANELS.positions.title, position: { direction: 'below' }, initialHeight: ACCOUNT_HEIGHT })
  api.addPanel({ id: 'orders', component: 'orders', title: PANELS.orders.title, position: { referencePanel: 'positions', direction: 'within' }, inactive: true })
  api.addPanel({ id: 'activity', component: 'activity', title: PANELS.activity.title, position: { referencePanel: 'positions', direction: 'within' }, inactive: true })
  api.addPanel({ id: 'watchlist', component: 'watchlist', title: PANELS.watchlist.title, position: { referencePanel: 'chart', direction: 'left' }, initialWidth: SIDE_WIDTH.watchlist })
  api.addPanel({ id: 'ticket', component: 'ticket', title: PANELS.ticket.title, position: { referencePanel: 'chart', direction: 'right' }, initialWidth: SIDE_WIDTH.ticket })
  api.getPanel('chart')?.api.setActive()
}

type Side = 'left' | 'right' | 'below' | 'within'

/** Where a panel belongs in the docked layout: beside (or tabbed with) a docked panel,
 *  or at an edge of the workspace. Floating panels never count as a reference, so a panel
 *  is never put inside a floating window by accident. */
export interface DockSpot {
  /** A docked panel to place it relative to; none means an edge of the whole workspace. */
  beside?: string
  side: Side
  /** Its default width (left/right) or height (below). */
  size?: number
}

export function dockSpot(api: DockviewApi, id: PanelId): DockSpot {
  const docked = (p: PanelId) => p !== id && api.getPanel(p)?.group.api.location.type === 'grid'
  if (ACCOUNT.includes(id)) {
    const other = ACCOUNT.find(docked)
    return other ? { beside: other, side: 'within' } : { side: 'below', size: ACCOUNT_HEIGHT }
  }
  if (id === 'watchlist') return docked('chart') ? { beside: 'chart', side: 'left', size: SIDE_WIDTH.watchlist } : { side: 'left', size: SIDE_WIDTH.watchlist }
  if (id === 'ticket') return docked('chart') ? { beside: 'chart', side: 'right', size: SIDE_WIDTH.ticket } : { side: 'right', size: SIDE_WIDTH.ticket }
  // The chart: between the watchlist and order entry when they're docked.
  if (docked('ticket')) return { beside: 'ticket', side: 'left' }
  if (docked('watchlist')) return { beside: 'watchlist', side: 'right' }
  return { side: 'right' }
}

/** Bring back a closed panel at its usual spot (or focus it if it's already open). */
export function reopenPanel(api: DockviewApi, id: PanelId): void {
  const existing = api.getPanel(id)
  if (existing) {
    showGroup(existing.group)
    existing.api.setActive()
    return
  }
  const spot = dockSpot(api, id)
  const position: AddPanelOptions['position'] = spot.beside
    ? { referencePanel: spot.beside, direction: spot.side }
    : { direction: spot.side === 'within' ? 'right' : spot.side }
  const size = spot.size === undefined ? {} : spot.side === 'below' ? { initialHeight: spot.size } : { initialWidth: spot.size }
  const panel = api.addPanel({ id, component: id, title: PANELS[id].title, position, ...size })
  showGroup(panel.group) // it may have joined a collapsed group
}

/** Expand a group collapsed to an edge strip, so a panel brought to it is seen. */
function showGroup(group: DockviewGroupPanel): void {
  if (group.api.location.type === 'grid' && !group.api.isVisible) group.api.setVisible(true)
}

/** Dock a floating group back at the usual spot of its (first) panel, at its default size. */
export function dockGroup(api: DockviewApi, group: DockviewGroupPanel): void {
  const id = group.panels[0]?.id as PanelId | undefined
  if (!id) return
  const spot = dockSpot(api, id)
  const toPosition = { left: 'left', right: 'right', below: 'bottom', within: 'center' } as const
  const target = spot.beside ? api.getPanel(spot.beside)?.group : undefined
  group.api.moveTo({ group: target, position: toPosition[spot.side] })
  if (target && spot.side === 'within') showGroup(target)
  if (spot.size !== undefined && spot.side !== 'within') {
    group.api.setSize(spot.side === 'below' ? { height: spot.size } : { width: spot.size })
  }
  if (id === 'chart') {
    // Docking splits the neighbour's space evenly; the chart is the main view, so the side
    // panels go back to their usual widths and the chart takes the rest.
    for (const side of ['watchlist', 'ticket'] as const) {
      const g = api.getPanel(side)?.group
      if (g && g !== group && g.api.location.type === 'grid') g.api.setSize({ width: SIDE_WIDTH[side] })
    }
  }
}

