/** A floating window's rectangle, in pixels within the workspace. */
export interface Box {
  left: number
  top: number
  width: number
  height: number
}

/** How close (px) an edge must come before it snaps. */
export const SNAP_DISTANCE = 12

/**
 * Where a dragged floating window should go: its proposed position, pulled flush
 * against the workspace edges or the edges of other floating windows when it comes
 * within `distance`. Each axis snaps on its own, to the nearest candidate.
 */
export function snapPosition(
  proposed: Box,
  container: { width: number; height: number },
  others: readonly Box[],
  distance = SNAP_DISTANCE,
): { left: number; top: number } {
  const xs = [0, container.width - proposed.width]
  const ys = [0, container.height - proposed.height]
  for (const o of others) {
    // Another window's edges only attract when the two are near each other on the other
    // axis (overlapping or within `distance`); otherwise a far-away window feels sticky.
    const nearVertically = proposed.top <= o.top + o.height + distance && proposed.top + proposed.height >= o.top - distance
    const nearHorizontally = proposed.left <= o.left + o.width + distance && proposed.left + proposed.width >= o.left - distance
    // Side by side (touching), or aligned with the other window's edges.
    if (nearVertically) xs.push(o.left + o.width, o.left - proposed.width, o.left, o.left + o.width - proposed.width)
    if (nearHorizontally) ys.push(o.top + o.height, o.top - proposed.height, o.top, o.top + o.height - proposed.height)
  }
  return { left: nearest(proposed.left, xs, distance), top: nearest(proposed.top, ys, distance) }
}

function nearest(value: number, candidates: number[], distance: number): number {
  let best = value
  let bestGap = distance + 1
  for (const c of candidates) {
    const gap = Math.abs(c - value)
    if (gap <= distance && gap < bestGap) {
      best = c
      bestGap = gap
    }
  }
  return best
}

/** The edges of a floating window being resized (the handle's sides). */
export interface ResizeEdges {
  left?: boolean
  right?: boolean
  top?: boolean
  bottom?: boolean
}

/**
 * A floating window being resized: the edges being dragged pull flush against the
 * workspace edges and against other floating windows (touching them, or lined up with
 * their edges) when within `distance`. The opposite edges stay put.
 */
export function snapResize(
  box: Box,
  edges: ResizeEdges,
  container: { width: number; height: number },
  others: readonly Box[],
  distance = SNAP_DISTANCE,
): Box {
  let x1 = box.left
  let x2 = box.left + box.width
  let y1 = box.top
  let y2 = box.top + box.height
  const xs = [0, container.width]
  const ys = [0, container.height]
  for (const o of others) {
    const nearVertically = y1 <= o.top + o.height + distance && y2 >= o.top - distance
    const nearHorizontally = x1 <= o.left + o.width + distance && x2 >= o.left - distance
    if (nearVertically) xs.push(o.left, o.left + o.width)
    if (nearHorizontally) ys.push(o.top, o.top + o.height)
  }
  if (edges.left) x1 = nearest(x1, xs, distance)
  if (edges.right) x2 = nearest(x2, xs, distance)
  if (edges.top) y1 = nearest(y1, ys, distance)
  if (edges.bottom) y2 = nearest(y2, ys, distance)
  return { left: x1, top: y1, width: x2 - x1, height: y2 - y1 }
}
