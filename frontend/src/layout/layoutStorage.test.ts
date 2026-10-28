import { describe, expect, it } from 'vitest'
import { loadLayout } from './layoutStorage'

const leaf = (id: string, view: string) => ({ type: 'leaf', data: { id, views: [view], activeView: view }, size: 100 })

describe('loadLayout', () => {
  it('drops panels that no longer exist, their emptied groups, and references to them', () => {
    const saved = {
      grid: { root: { type: 'branch', data: [{ type: 'branch', data: [leaf('1', 'watchlist'), leaf('2', 'activity')], size: 300 }, leaf('3', 'chart')] }, orientation: 'HORIZONTAL', width: 1000, height: 800 },
      panels: {
        watchlist: { id: 'watchlist', contentComponent: 'watchlist' },
        activity: { id: 'activity', contentComponent: 'activity' },
        chart: { id: 'chart', contentComponent: 'chart' },
        orders: { id: 'orders', contentComponent: 'orders' },
      },
      activeGroup: '2',
      floatingGroups: [{ data: { id: '4', views: ['activity', 'orders'], activeView: 'activity' }, position: {} }],
      popoutGroups: [{ data: { id: '5', views: ['activity'], activeView: 'activity' } }],
    }
    localStorage.setItem('paper-trade.layout.v3', JSON.stringify(saved))
    const out = loadLayout(new Set(['watchlist', 'chart', 'orders']))!
    expect(Object.keys(out.panels)).toEqual(['watchlist', 'chart', 'orders'])
    const column = (out.grid.root.data as { data: { data: { id: string } }[] }[])[0].data
    expect(column.map((n) => n.data.id)).toEqual(['1'])
    expect(out.activeGroup).toBeUndefined()
    expect(out.floatingGroups).toEqual([{ data: { id: '4', views: ['orders'], activeView: 'orders' }, position: {} }])
    expect(out.popoutGroups).toEqual([])
  })

  it('leaves a layout with only known panels as it is', () => {
    const saved = { grid: { root: { type: 'branch', data: [leaf('1', 'chart')] }, orientation: 'HORIZONTAL', width: 1, height: 1 }, panels: { chart: { id: 'chart', contentComponent: 'chart' } }, activeGroup: '1' }
    localStorage.setItem('paper-trade.layout.v3', JSON.stringify(saved))
    expect(loadLayout(new Set(['chart']))).toEqual(saved)
  })
})
