import { memo } from 'react'

const SESSION_MINUTES = 390

interface Props {
  /** Today's prices so far (the last is replaced by `last`, the live price). */
  points: number[] | undefined
  last: number
  prevClose: number
  step: number
}

const W = 64
const H = 18

/** A tiny line of today's price across the whole session (it fills in as the day
 *  goes), against a dotted line at the previous close. */
export const Sparkline = memo(function Sparkline({ points, last, prevClose, step }: Props) {
  if (!points?.length) return <svg className="sparkline" width={W} height={H} aria-hidden="true" />
  const values = [...points.slice(0, -1), last]
  const lo = Math.min(prevClose, ...values)
  const hi = Math.max(prevClose, ...values)
  const span = hi - lo || 1
  const x = (i: number) => (Math.min(i, SESSION_MINUTES / step) / (SESSION_MINUTES / step)) * (W - 1) + 0.5
  const y = (v: number) => H - 1.5 - ((v - lo) / span) * (H - 3)
  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const dir = last > prevClose ? 'up' : last < prevClose ? 'down' : 'flat'
  return (
    <svg className={`sparkline ${dir}`} width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <line className="spark-base" x1="0" x2={W} y1={y(prevClose)} y2={y(prevClose)} />
      <polyline points={line} />
    </svg>
  )
})

/** Where the last price sits between the low and the high. */
export function RangeBar({ low, high, value, label }: { low: number; high: number; value: number; label: string }) {
  const pos = high > low ? Math.min(1, Math.max(0, (value - low) / (high - low))) : 0.5
  return (
    <span className="range-bar" role="img" aria-label={label} title={label}>
      <span className="range-marker" style={{ left: `${pos * 100}%` }} />
    </span>
  )
}
