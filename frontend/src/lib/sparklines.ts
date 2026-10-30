import { useEffect, useRef, useState } from 'react'
import { fetchSparklines } from './api'
import type { ClockState } from './types'

const MIN_REFRESH_MS = 2000 // at high speeds, at most this often

/** Today's prices every few minutes for `symbols`, refetched when a new stretch
 *  of minutes starts (at most every 2s), the list changes or time jumps. */
export function useSparklines(symbols: string[], clock: ClockState | null, enabled: boolean) {
  const [data, setData] = useState<{ step: number; series: Record<string, number[]> }>({ step: 5, series: {} })
  const last = useRef(0)
  const key = symbols.join(',')
  const step = data.step
  const bucket = clock ? Math.floor((Date.parse(clock.time) - Date.parse(clock.session_open)) / (step * 60_000)) : 0

  useEffect(() => {
    if (!enabled || !clock || !key) return
    let live = true
    const wait = Math.max(0, MIN_REFRESH_MS - (Date.now() - last.current))
    const timer = setTimeout(() => {
      last.current = Date.now()
      fetchSparklines(key.split(','))
        .then((d) => live && setData({ step: d.step_minutes, series: d.series }))
        .catch(() => {})
    }, wait)
    return () => {
      live = false
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch on these, not every tick
  }, [enabled, key, bucket, clock?.session_open, clock?.timeline])

  return data
}
