import { useEffect, useState } from 'react'
import { typing } from '../lib/keyboard'

/** Full screen hides the OS and browser bars, which can take 100px or more from the chart.
 *  F toggles it too (Esc leaves, as browsers always allow). */
export function FullscreenButton() {
  const [full, setFull] = useState(() => typeof document !== 'undefined' && !!document.fullscreenElement)

  useEffect(() => {
    if (!supported()) return
    const onChange = () => setFull(!!document.fullscreenElement)
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'f' || e.repeat || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return
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

  if (!supported()) return null
  return (
    <button className="ctl icon-ctl" onClick={toggle} title={full ? 'Exit full screen (F)' : 'Full screen (F)'} aria-label={full ? 'Exit full screen' : 'Full screen'} aria-pressed={full}>
      {full ? '⤡' : '⛶'}
    </button>
  )
}

const supported = () =>
  typeof document !== 'undefined' && document.fullscreenEnabled && typeof document.documentElement.requestFullscreen === 'function'

function toggle() {
  // Either can be refused (for example without a user gesture); then nothing changes.
  if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
  else void document.documentElement.requestFullscreen().catch(() => {})
}
