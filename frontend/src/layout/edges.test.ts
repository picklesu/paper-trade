import { Orientation, type SerializedDockview } from 'dockview-react'
import { describe, expect, it } from 'vitest'
import { edgeOf, savedSizes, withHidden } from './edges'

const leaf = (id: string, visible?: boolean) => ({ type: 'leaf' as const, data: { id, views: [id], activeView: id }, size: 100, ...(visible === undefined ? {} : { visible }) })
const branch = (...data: unknown[]) => ({ type: 'branch' as const, data, size: 100 })

// The default workspace: a column of [watchlist | chart | ticket] above the account tabs.
const layout = {
  grid: {
    root: branch(branch(leaf('w'), leaf('c'), leaf('t')), leaf('a')),
    orientation: Orientation.VERTICAL,
    width: 1000,
    height: 800,
  },
  panels: {},
} as unknown as SerializedDockview

describe('edgeOf', () => {
  it('puts a group on the side of the chart it sits on', () => {
    expect(edgeOf(layout, 'w', 'c')).toBe('left')
    expect(edgeOf(layout, 't', 'c')).toBe('right')
    expect(edgeOf(layout, 'a', 'c')).toBe('bottom')
  })

  it('finds the side where the paths split, however deep the group is', () => {
    const nested = { ...layout, grid: { ...layout.grid, root: branch(leaf('top'), branch(branch(leaf('w1'), leaf('w2')), leaf('c'))) } } as unknown as SerializedDockview
    expect(edgeOf(nested, 'w2', 'c')).toBe('left')
    expect(edgeOf(nested, 'top', 'c')).toBe('top')
  })

  it('without a docked chart, uses the nearer side of the workspace', () => {
    expect(edgeOf(layout, 'w', undefined)).toBe('top')
    expect(edgeOf(layout, 'a', undefined)).toBe('bottom')
  })
})

describe('withHidden', () => {
  it('marks the given groups hidden and leaves the rest', () => {
    const out = withHidden(layout, new Set(['t']))
    const row = (out.grid.root.data as { data: { data: { id: string }; visible?: boolean }[] }[])[0].data
    expect(row.map((n) => n.visible)).toEqual([undefined, undefined, false])
    expect(withHidden(layout, new Set())).toBe(layout)
  })
})

describe('withHidden, sizes', () => {
  it("gives a hidden group's space to the chart's side of its branch", () => {
    const sized = {
      ...layout,
      grid: { ...layout.grid, root: { type: 'branch', size: 1000, data: [branch({ ...leaf('w'), size: 250 }, { ...leaf('c'), size: 450 }, { ...leaf('t'), size: 300 }), leaf('a')] } },
    } as unknown as SerializedDockview
    const row = (withHidden(sized, new Set(['w']), 'c').grid.root.data as { data: { size: number; visible?: boolean }[] }[])[0].data
    expect(row.map((n) => [n.size, n.visible])).toEqual([[250, false], [700, undefined], [300, undefined]])
  })
})

describe('savedSizes', () => {
  it('reads widths in rows and heights in columns', () => {
    const sized = {
      ...layout,
      grid: { ...layout.grid, root: { type: 'branch', size: 1000, data: [branch({ ...leaf('w'), size: 250 }, { ...leaf('c'), size: 450 }), { ...leaf('a'), size: 185 }] } },
    } as unknown as SerializedDockview
    const sizes = savedSizes(sized)
    expect(sizes.get('w')).toEqual({ width: 250 })
    expect(sizes.get('a')).toEqual({ height: 185 })
  })
})
