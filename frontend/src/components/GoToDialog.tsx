import { useState } from 'react'
import { goto, previewGoto, type GotoResult } from '../lib/api'
import { marketDateTime, shiftInputDays, toMarketInput } from '../lib/format'
import type { ClockState } from '../lib/types'

interface Props {
  clock: ClockState
  onClose: () => void
}

const SHORTCUTS = [
  { label: '−1 week', days: -7 },
  { label: '−1 day', days: -1 },
  { label: '+1 day', days: 1 },
  { label: '+1 week', days: 7 },
  { label: '+1 month', days: 30 },
]

/** Pick a market date/time, then confirm what going there will do. */
export function GoToDialog({ clock, onClose }: Props) {
  const [value, setValue] = useState(() => toMarketInput(clock.time))
  const [preview, setPreview] = useState<GotoResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (future: 'replay' | 'new' = 'replay') => {
    setBusy(true)
    setError(null)
    try {
      await goto(value, future)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const check = async () => {
    setError(null)
    try {
      const result = await previewGoto(value)
      if (result.action === 'none') {
        onClose()
      } else if (result.action === 'forward') {
        await run()
      } else {
        setPreview(result)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="goto-title"
        onClick={(e) => e.stopPropagation()}
      >
        {preview?.action === 'rewind' ? (
          <>
            <h2 id="goto-title">Go back to {marketDateTime(preview.target)}?</h2>
            <ul className="dialog-list">
              <li>Trades made after this time will be erased, and their money refunded.</li>
              <li>Positions you hold will be valued at that time's prices.</li>
            </ul>
            <p className="muted">Trading arrives in Phase 2b, so there's nothing to erase yet.</p>
            <p>What should happen after that?</p>
            <div className="dialog-actions">
              <button className="btn primary" disabled={busy} onClick={() => run('replay')}>
                Replay the same prices
              </button>
              <button className="btn primary" disabled={busy} onClick={() => run('new')}>
                Generate a new future
              </button>
              <button className="btn" disabled={busy} onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        ) : preview?.action === 'reset' ? (
          <>
            <h2 id="goto-title">Start a new market on {marketDateTime(preview.target)}?</h2>
            <p>
              That's before this market's history begins ({marketDateTime(clock.origin)}). Going there starts a
              brand-new market: all price history and trades are erased, and cash resets to the starting amount.
            </p>
            <div className="dialog-actions">
              <button className="btn danger" disabled={busy} onClick={() => run()}>
                Start new market
              </button>
              <button className="btn" disabled={busy} onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 id="goto-title">Go to date and time</h2>
            <label className="field">
              <span>Market time (New York)</span>
              <input
                type="datetime-local"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                aria-label="Market date and time"
              />
            </label>
            <div className="shortcuts">
              {SHORTCUTS.map((s) => (
                <button key={s.label} className="ctl" onClick={() => setValue((v) => shiftInputDays(v, s.days))}>
                  {s.label}
                </button>
              ))}
            </div>
            <p className="muted">
              The market is open 9:30 AM–4:00 PM ET, Monday–Friday. Other times go to the next open.
            </p>
            <div className="dialog-actions">
              <button className="btn primary" disabled={busy || !value} onClick={check}>
                Go
              </button>
              <button className="btn" disabled={busy} onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  )
}
