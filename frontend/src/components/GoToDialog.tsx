import { useState } from 'react'
import { Modal } from './Modal'
import { goto, previewGoto, triggerShock, type GotoResult } from '../lib/api'
import { calendarDate, dollars, marketDateTime, marketStamp, price, shiftInputDays, toMarketInput } from '../lib/format'
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
  // The input has minute precision, so the starting value is slightly in the past.
  const [initial] = useState(() => toMarketInput(clock.time))
  const [value, setValue] = useState(initial)
  const [preview, setPreview] = useState<GotoResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Set while offering to trigger an uncertainty shock.
  const [shockView, setShockView] = useState(false)

  // The next session is still in the real past: prices there are real, so there is nothing to shock.
  const today = toMarketInput(clock.time).slice(0, 10)
  const inRealPast = !!clock.real_through && today < clock.real_through

  const shock = async () => {
    setBusy(true)
    setError(null)
    try {
      await triggerShock()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

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
    // Unchanged means "stay here", not "go back to the start of this minute".
    if (value === initial) {
      onClose()
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await previewGoto(value)
      if (result.action === 'none') {
        onClose()
      } else if (result.action === 'forward') {
        await run()
      } else {
        setPreview(result)
        setBusy(false)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <Modal labelledBy="goto-title" onClose={onClose}>
      {shockView ? (
        <>
          <h2 id="goto-title">Trigger an uncertainty shock?</h2>
          <p>
            At the next open, uncertainty jumps: volatility rises to 1.4 to 1.9 times
            normal and fades over the following months, the market opens lower, and option prices and the VIX
            go up with it. It is the kind of shock Nick Bloom's research describes (9/11, the 2008 crisis, Brexit). Shocks also
            arrive on their own about once every three years. Going back to before you triggered it undoes it.
          </p>
          {inRealPast && (
            <p className="warn-text">
              This date is in the real past (the real daily prices run through {calendarDate(clock.real_through ?? '', true)}), where
              prices follow what really happened, so a shock can't be added. Go to a later date first.
            </p>
          )}
          <div className="dialog-actions">
            <button className="btn primary" disabled={busy || inRealPast} onClick={shock}>
              Trigger it
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() => {
                setError(null)
                setShockView(false)
              }}
            >
              Back
            </button>
          </div>
        </>
      ) : preview?.action === 'rewind' ? (
        <>
          <h2 id="goto-title">Go back to {marketDateTime(preview.target)}?</h2>
          <RewindSummary preview={preview} />
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
            brand-new market: all price history and trades are erased, and cash resets to{' '}
            {dollars(preview.starting_cash ?? 0)}.
          </p>
          {(preview.erased_orders ?? 0) > 0 && (
            <p>
              {preview.erased_orders} order{preview.erased_orders === 1 ? '' : 's'} (
              {preview.erased_fills?.length ?? 0} trade{preview.erased_fills?.length === 1 ? '' : 's'}) will be
              erased.
            </p>
          )}
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
            {clock.real_through && (
              <button className="ctl" title={`The close of ${calendarDate(clock.real_through, true)}`} onClick={() => setValue(`${clock.real_through}T15:59`)}>
                Last real day
              </button>
            )}
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
          <div className="dialog-footer">
            <button className="link-button" disabled={busy} onClick={() => setShockView(true)}>
              Trigger an uncertainty shock…
            </button>
          </div>
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  )
}

const MAX_LISTED = 8

function RewindSummary({ preview }: { preview: GotoResult }) {
  const fills = preview.erased_fills ?? []
  const positions = preview.positions ?? []
  const erasedOrders = preview.erased_orders ?? 0
  return (
    <>
      {fills.length === 0 && erasedOrders === 0 ? (
        <p className="muted">No trades or orders after this time, so nothing is erased.</p>
      ) : (
        <>
          <p>
            {fills.length === 1
              ? 'This trade will be erased and refunded:'
              : fills.length
                ? `These ${fills.length} trades will be erased and refunded:`
                : 'No trades will be erased.'}
          </p>
          {fills.length > 0 && (
            <ul className="dialog-list">
              {fills.slice(0, MAX_LISTED).map((o) => (
                <li key={o.id}>
                  {o.side === 'buy' ? 'Bought' : 'Sold'} {o.qty} {o.symbol} at {price(o.fill_price!)} ({marketStamp(o.filled_at!)})
                </li>
              ))}
              {fills.length > MAX_LISTED && <li>…and {fills.length - MAX_LISTED} more</li>}
            </ul>
          )}
          {erasedOrders > 0 && (
            <p className="muted">
              {erasedOrders} order{erasedOrders === 1 ? '' : 's'} placed after this time will be removed. Orders that were
              open then become open again.
            </p>
          )}
          {preview.cash_then !== undefined && preview.cash_now !== undefined && preview.cash_then !== preview.cash_now && (
            <p>
              Cash goes from {dollars(preview.cash_now)} to {dollars(preview.cash_then)}.
            </p>
          )}
        </>
      )}
      {positions.length > 0 && (
        <>
          <p>Positions you held then are valued at that time's prices:</p>
          <table className="grid-table compact">
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="num">Qty</th>
                <th className="num">Price now</th>
                <th className="num">Price then</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.symbol}>
                  <td className="sym">{p.symbol}</td>
                  <td className="num">{p.qty.toLocaleString()}</td>
                  <td className="num">{price(p.price_now)}</td>
                  <td className="num">{price(p.price_then)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  )
}
