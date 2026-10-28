import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface Props {
  labelledBy: string
  onClose: () => void
  children: ReactNode
}

/** A dialog over the whole app (rendered at the page level, so floating windows and
 *  panels never cover it). Clicking the backdrop closes it. */
export function Modal({ labelledBy, onClose, children }: Props) {
  return createPortal(
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={labelledBy} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>,
    document.body,
  )
}
