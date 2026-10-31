import { useState } from 'react'
import { updateClock } from '../lib/api'
import { marketDate, marketTime } from '../lib/format'
import type { ClockState, ProgressMessage, Regime } from '../lib/types'
import { GoToDialog } from './GoToDialog'

interface Props {
  clock: ClockState | null
  regime: Regime | null
  /** Extra volatility from an uncertainty shock in force (0: none). */
  shock?: number
  /** A triggered shock that begins at the next open. */
  shockPending?: boolean
  progress: ProgressMessage | null
}

const REGIME_LABEL: Record<Regime, string> = {
  calm: 'Calm',
  normal: 'Normal',
  volatile: 'Volatile',
}

export function ClockControls({ clock, regime, shock = 0, shockPending = false, progress }: Props) {
  const [showGoto, setShowGoto] = useState(false)
  const disabled = clock === null || progress !== null
  const send = (update: { speed?: number; paused?: boolean }) => {
    updateClock(update).catch((err) => console.error(err))
  }

  return (
    <div className="clock-controls">
      <div className="clock" title="Simulated market time (New York)">
        <span className="clock-date">{clock ? marketDate(clock.time) : '---'}</span>
        <span className="clock-time">{clock ? marketTime(clock.time) : '--:--:--'}</span>
        <span className="clock-tz">ET</span>
      </div>
      <button className="ctl" disabled={disabled} onClick={() => setShowGoto(true)}>
        Go to…
      </button>
      <button
        className="ctl"
        disabled={disabled}
        onClick={() => send({ paused: !clock?.paused })}
        aria-label={clock?.paused ? 'Resume market' : 'Pause market'}
      >
        {clock?.paused ? '▶' : '❚❚'}
      </button>
      <div className="speeds" role="group" aria-label="Market speed">
        {(clock?.speeds ?? []).map((s) => (
          <button
            key={s}
            className={clock?.speed === s ? 'ctl active' : 'ctl'}
            disabled={disabled}
            aria-pressed={clock?.speed === s}
            onClick={() => send({ speed: s })}
          >
            {s}×
          </button>
        ))}
      </div>
      {progress ? (
        <span className="regime" role="progressbar" aria-valuenow={progress.done} aria-valuemax={progress.total}>
          Fast-forwarding… {Math.round((progress.done / progress.total) * 100)}%
        </span>
      ) : (
        regime && (
          <span className={`regime regime-${regime}`} title="Current market volatility">
            {REGIME_LABEL[regime]}
          </span>
        )
      )}
      {!progress && (shock > 0 || shockPending) && (
        <span
          className="regime regime-shock"
          title={shockPending ? 'An uncertainty shock begins at the next open' : 'An uncertainty shock: volatility is higher and fading'}
        >
          {shockPending ? 'Shock at open' : 'Shock'}
        </span>
      )}
      {showGoto && clock && <GoToDialog clock={clock} onClose={() => setShowGoto(false)} />}
    </div>
  )
}
