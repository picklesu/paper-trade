import { Orientation, type SerializedDockview } from 'dockview-react'
import { describe, expect, it } from 'vitest'
import { edgeOf, savedSizes } from './edges'

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

describe('savedSizes', () => {
  it('reads each group\'s size along its branch and the branch\'s size across', () => {
    const sized = {
      ...layout,
      grid: { ...layout.grid, root: { type: 'branch', size: 1000, data: [branch({ ...leaf('w'), size: 250 }, { ...leaf('c'), size: 450 }), { ...leaf('a'), size: 185 }] } },
    } as unknown as SerializedDockview
    const sizes = savedSizes(sized)
    expect(sizes.get('w')).toEqual({ width: 250, height: 100 })
    expect(sizes.get('a')).toEqual({ width: 1000, height: 185 })
  })
})
