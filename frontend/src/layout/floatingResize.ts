import type { DockviewApi } from 'dockview-react'
import { type Box, type ResizeEdges, snapResize } from './snap'

const relative = (el: Element, container: DOMRect): Box => {
  const r = el.getBoundingClientRect()
  return { left: r.left - container.left, top: r.top - container.top, width: r.width, height: r.height }
}

/**
 * dockview resizes floating windows from their edge handles but neither snaps them nor
 * brings them to the front (only activating a window does). So: bring the window forward
 * when a handle is grabbed, and after each of dockview's resize steps, pull the dragged
 * edges flush against the workspace and other floating windows.
 *
 * Our listeners are added on `window` while the pointerdown bubbles, after dockview's
 * handle has added its own pointermove listener, so ours runs after it on every move.
 * dockview reads the final bounds from the window's inline style when the resize ends.
 */
export function snapFloatingResizes(api: DockviewApi): () => void {
  const onDown = (e: PointerEvent) => {
    const handle = (e.target as Element | null)?.closest?.('[class*="dv-resize-handle-"]')
    const overlay = handle?.parentElement
    const container = overlay?.parentElement
    if (!handle || !overlay?.classList.contains('dv-resize-container') || !container) return
    const direction = [...handle.classList].find((c) => c.startsWith('dv-resize-handle-'))!.slice('dv-resize-handle-'.length)
    const edges: ResizeEdges = {
      left: direction.includes('left'),
      right: direction.includes('right'),
      top: direction.includes('top'),
      bottom: direction.includes('bottom'),
    }
    api.groups.find((g) => g.api.location.type === 'floating' && overlay.contains(g.element))?.activePanel?.api.setActive()

    const onMove = () => {
      const area = container.getBoundingClientRect()
      const others = [...container.querySelectorAll(':scope > .dv-resize-container')]
        .filter((o) => o !== overlay && !o.classList.contains('dv-hidden'))
        .map((o) => relative(o, area))
      const box = relative(overlay, area)
      const snapped = snapResize(box, edges, area, others)
      if (snapped.left === box.left && snapped.top === box.top && snapped.width === box.width && snapped.height === box.height) return
      const style = overlay.style
      style.width = `${snapped.width}px`
      style.height = `${snapped.height}px`
      // Keep whichever side dockview anchors the window by.
      if (style.top !== 'auto') style.top = `${snapped.top}px`
      else style.bottom = `${area.height - snapped.top - snapped.height}px`
      if (style.left !== 'auto') style.left = `${snapped.left}px`
      else style.right = `${area.width - snapped.left - snapped.width}px`
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }
  window.addEventListener('pointerdown', onDown)
  return () => window.removeEventListener('pointerdown', onDown)
}
