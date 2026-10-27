import type { ClockState, GotoAction } from './types'

export async function updateClock(update: { speed?: number; paused?: boolean }): Promise<ClockState> {
  const resp = await fetch('/api/clock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  })
  if (!resp.ok) throw new Error(`clock update failed: ${resp.status}`)
  return resp.json()
}

export interface GotoResult {
  action: GotoAction
  /** Where the market lands, after snapping to market hours. */
  target: string
}

/** `time` is New York wall-clock time, e.g. "2026-03-02T10:30". */
export async function previewGoto(time: string): Promise<GotoResult> {
  const resp = await fetch(`/api/clock/goto?time=${encodeURIComponent(time)}`)
  if (!resp.ok) throw new Error(`goto preview failed: ${resp.status}`)
  return resp.json()
}

export async function goto(time: string, future: 'replay' | 'new' = 'replay'): Promise<GotoResult> {
  const resp = await fetch('/api/clock/goto', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ time, future }),
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `goto failed: ${resp.status}`)
  }
  return resp.json()
}
