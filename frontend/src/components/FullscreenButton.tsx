import { useEffect, useState } from 'react'

const typing = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))

/** Full screen hides the OS and browser bars, which can take 100px or more from the chart.
 *  F toggles it too (Esc leaves, as browsers always allow). */
export function FullscreenButton() {
  const [full, setFull] = useState(() => typeof document !== 'undefined' && !!document.fullscreenElement)

  useEffect(() => {
    const onChange = () => setFull(!!document.fullscreenElement)
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'f' || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return
      e.preventDefault()
      toggle()
    }
    document.addEventListener('fullscreenchange', onChange)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('fullscreenchange', onChange)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  if (typeof document === 'undefined' || !document.fullscreenEnabled) return null
  return (
    <button className="ctl icon-ctl" onClick={toggle} title={full ? 'Exit full screen (F)' : 'Full screen (F)'} aria-label={full ? 'Exit full screen' : 'Full screen'} aria-pressed={full}>
      {full ? '⤡' : '⛶'}
    </button>
  )
}

function toggle() {
  if (document.fullscreenElement) void document.exitFullscreen()
  else void document.documentElement.requestFullscreen().catch(() => {})
}
