import type { AddPanelOptions, DockviewApi } from 'dockview-react'
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

/** The default workspace: chart in the middle, watchlist left, order entry right,
 *  and the account views tabbed along the bottom. */
export function buildDefaultLayout(api: DockviewApi): void {
  api.clear()
  api.addPanel({ id: 'chart', component: 'chart', title: PANELS.chart.title })
  api.addPanel({ id: 'positions', component: 'positions', title: PANELS.positions.title, position: { direction: 'below' }, initialHeight: 220 })
  api.addPanel({ id: 'orders', component: 'orders', title: PANELS.orders.title, position: { referencePanel: 'positions', direction: 'within' }, inactive: true })
  api.addPanel({ id: 'activity', component: 'activity', title: PANELS.activity.title, position: { referencePanel: 'positions', direction: 'within' }, inactive: true })
  api.addPanel({ id: 'watchlist', component: 'watchlist', title: PANELS.watchlist.title, position: { referencePanel: 'chart', direction: 'left' }, initialWidth: 250 })
  api.addPanel({ id: 'ticket', component: 'ticket', title: PANELS.ticket.title, position: { referencePanel: 'chart', direction: 'right' }, initialWidth: 300 })
  api.getPanel('chart')?.api.setActive()
}

/** Where a closed panel comes back: its default spot if the panel it sits beside is
 *  open, else docked at that side of the workspace. */
export function reopenPanel(api: DockviewApi, id: PanelId): void {
  if (api.getPanel(id)) {
    api.getPanel(id)!.api.setActive()
    return
  }
  const open = (p: PanelId) => api.getPanel(p) !== undefined
  let position: AddPanelOptions['position']
  let size: { initialWidth?: number; initialHeight?: number } = {}
  if (ACCOUNT.includes(id) && ACCOUNT.some(open)) {
    position = { referencePanel: ACCOUNT.find(open)!, direction: 'within' }
  } else if (ACCOUNT.includes(id)) {
    position = { direction: 'below' }
    size = { initialHeight: 220 }
  } else if (id === 'watchlist') {
    position = open('chart') ? { referencePanel: 'chart', direction: 'left' } : { direction: 'left' }
    size = { initialWidth: 250 }
  } else if (id === 'ticket') {
    position = open('chart') ? { referencePanel: 'chart', direction: 'right' } : { direction: 'right' }
    size = { initialWidth: 300 }
  } else {
    // The chart: between the watchlist and order entry when they're docked.
    position = open('ticket') ? { referencePanel: 'ticket', direction: 'left' } : { direction: 'right' }
  }
  api.addPanel({ id, component: id, title: PANELS[id].title, position, ...size })
}
