import { SPEEDS, updateClock } from '../lib/api'
import { marketDate, marketTime } from '../lib/format'
import type { ClockState, Regime } from '../lib/types'

interface Props {
  clock: ClockState | null
  regime: Regime | null
}

const REGIME_LABEL: Record<Regime, string> = {
  calm: 'Calm',
  normal: 'Normal',
  volatile: 'Volatile',
}

export function ClockControls({ clock, regime }: Props) {
  const disabled = clock === null
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
      <button
        className="ctl"
        disabled={disabled}
        onClick={() => send({ paused: !clock?.paused })}
        aria-label={clock?.paused ? 'Resume market' : 'Pause market'}
      >
        {clock?.paused ? '▶' : '❚❚'}
      </button>
      <div className="speeds" role="group" aria-label="Market speed">
        {SPEEDS.map((s) => (
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
      {regime && (
        <span className={`regime regime-${regime}`} title="Current market volatility">
          {REGIME_LABEL[regime]}
        </span>
      )}
    </div>
  )
}
