import { describe, expect, it } from 'vitest'
import { snapPosition } from './snap'

const container = { width: 1200, height: 800 }
const box = (left: number, top: number, width = 300, height = 400) => ({ left, top, width, height })

describe('snapPosition', () => {
  it('leaves the window alone away from edges', () => {
    expect(snapPosition(box(200, 150), container, [])).toEqual({ left: 200, top: 150 })
  })

  it('snaps to the workspace edges', () => {
    expect(snapPosition(box(8, 790 - 400), container, [])).toEqual({ left: 0, top: 400 })
    expect(snapPosition(box(1200 - 300 - 10, 5), container, [])).toEqual({ left: 900, top: 0 })
  })

  it('snaps flush against another window, and aligns with it', () => {
    const other = box(500, 100)
    // Just right of the other window, a little lower than its top.
    expect(snapPosition(box(806, 109), container, [other])).toEqual({ left: 800, top: 100 })
    // Just left of it.
    expect(snapPosition(box(195, 300), container, [other])).toEqual({ left: 200, top: 300 })
  })

  it('picks the nearest candidate', () => {
    // Both the right edge (900) and another window's left-align (895) are in range.
    expect(snapPosition(box(897, 300), container, [box(895, 20, 100, 100)])).toEqual({ left: 895, top: 300 })
  })
})
