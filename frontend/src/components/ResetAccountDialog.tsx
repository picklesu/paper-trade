import { useState } from 'react'
import { Modal } from './Modal'
import { resetAccount } from '../lib/api'

interface Props {
  startingCash: number
  onClose: () => void
}

export function ResetAccountDialog({ startingCash, onClose }: Props) {
  const [cash, setCash] = useState(String(startingCash))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await resetAccount(Number(cash))
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <Modal labelledBy="reset-title" onClose={onClose}>
      <h2 id="reset-title">Reset account?</h2>
      <p>
        All orders, trades and positions are erased and the account starts over with fresh cash. The market keeps
        its current date and prices.
      </p>
      <label className="field">
        <span>Starting cash ($)</span>
        <input type="number" min={1} step={1000} value={cash} onChange={(e) => setCash(e.target.value)} />
      </label>
      <div className="dialog-actions">
        <button className="btn danger" disabled={busy || !(Number(cash) > 0)} onClick={confirm}>
          Reset account
        </button>
        <button className="btn" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  )
}
