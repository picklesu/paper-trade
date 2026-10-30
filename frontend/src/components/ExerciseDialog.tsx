import { useEffect, useState } from 'react'
import { exerciseOptions, previewExercise } from '../lib/api'
import { dollars, price } from '../lib/format'
import type { ExercisePreview, Position } from '../lib/types'
import { Modal } from './Modal'

/** Exercise long contracts now: how many, what it does to shares and cash, then confirm. */
export function ExerciseDialog({ position, onClose }: { position: Position; onClose: () => void }) {
  const option = position.option!
  const [qty, setQty] = useState(String(position.qty))
  // The preview (or why there's none) for the number of contracts it was asked for.
  const [answer, setAnswer] = useState<{ n: number; preview?: ExercisePreview; error?: string } | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const n = Number(qty)
  const current = answer?.n === n ? answer : null
  const preview = current?.preview ?? null
  const error = failed ?? current?.error ?? null

  useEffect(() => {
    let live = true
    if (!(n > 0)) return
    previewExercise(position.symbol, n)
      .then((p) => live && setAnswer({ n, preview: p }))
      .catch((err) => live && setAnswer({ n, error: err instanceof Error ? err.message : String(err) }))
    return () => {
      live = false
    }
  }, [position.symbol, n])

  const exercise = async () => {
    setBusy(true)
    setFailed(null)
    try {
      await exerciseOptions(position.symbol, n)
      onClose()
    } catch (err) {
      setFailed(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const call = option.right === 'call'
  return (
    <Modal onClose={onClose} labelledBy="exercise-title">
      <h2 id="exercise-title">Exercise {option.label}</h2>
      <label className="field">
        <span>Contracts (you hold {position.qty.toLocaleString()})</span>
        <input type="number" min={1} max={position.qty} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
      </label>
      {preview && (
        <>
          <p>
            {call ? 'Buy' : 'Sell'} {Math.abs(preview.shares).toLocaleString()} shares of {option.underlying} at {price(preview.strike)}:{' '}
            {call ? 'pay' : 'receive'} {dollars(Math.abs(preview.cash_change))} (cash {dollars(preview.cash_before)} → {dollars(preview.cash_after)}).
          </p>
          {preview.warnings.length > 0 && (
            <ul className="review-warnings">
              {preview.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </>
      )}
      {error && (
        <p className="warn-text" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button className="btn primary" disabled={busy || !preview} onClick={exercise}>
          Exercise
        </button>
        <button className="btn" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  )
}
