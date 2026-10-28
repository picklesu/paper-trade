import { Orientation, type SerializedDockview } from 'dockview-react'

/** The workspace edge a collapsed group's strip sits on. */
export type Edge = 'left' | 'right' | 'top' | 'bottom'

type Node = SerializedDockview['grid']['root']
const flip = (o: Orientation): Orientation => (o === Orientation.HORIZONTAL ? Orientation.VERTICAL : Orientation.HORIZONTAL)

type Step = { index: number; orientation: Orientation; count: number }

/** Each grid group's path of child indexes from the root, with the orientation of every
 *  branch along it (a HORIZONTAL branch lays its children out left to right). */
function paths(grid: SerializedDockview['grid']): Map<string, Step[]> {
  const out = new Map<string, Step[]>()
  const walk = (node: Node, orientation: Orientation, path: Step[]) => {
    if (node.type === 'leaf') {
      out.set((node.data as { id: string }).id, path)
      return
    }
    const children = node.data as Node[]
    children.forEach((child, index) => walk(child, flip(orientation), [...path, { index, orientation, count: children.length }]))
  }
  walk(grid.root, grid.orientation, [])
  return out
}

/** Which edge a docked group collapses to: the side of the chart it sits on, found where
 *  their paths through the grid split. Without a docked chart, the side of the workspace
 *  it's nearer to. */
export function edgeOf(layout: SerializedDockview, groupId: string, chartGroupId: string | undefined): Edge {
  const all = paths(layout.grid)
  const mine = all.get(groupId) ?? []
  const chart = chartGroupId && chartGroupId !== groupId ? all.get(chartGroupId) : undefined
  let i = 0
  if (chart) while (i < mine.length - 1 && i < chart.length - 1 && mine[i].index === chart[i].index) i++
  const step = mine[i]
  if (!step) return 'left' // the only group
  const before = chart ? step.index < chart[i].index : step.index < step.count / 2
  if (step.orientation === Orientation.HORIZONTAL) return before ? 'left' : 'right'
  return before ? 'top' : 'bottom'
}

/** A copy of the layout with these groups hidden, so a panel the user is only peeking
 *  at is saved collapsed. */
export function withHidden(layout: SerializedDockview, ids: ReadonlySet<string>): SerializedDockview {
  if (!ids.size) return layout
  const hide = (node: Node): Node =>
    node.type === 'leaf'
      ? ids.has((node.data as { id: string }).id)
        ? { ...node, visible: false }
        : node
      : { ...node, data: (node.data as Node[]).map(hide) }
  return { ...layout, grid: { ...layout.grid, root: hide(layout.grid.root) } }
}
