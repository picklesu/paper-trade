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
    // Side by side (touching), or aligned with the other window's edges.
    xs.push(o.left + o.width, o.left - proposed.width, o.left, o.left + o.width - proposed.width)
    ys.push(o.top + o.height, o.top - proposed.height, o.top, o.top + o.height - proposed.height)
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
