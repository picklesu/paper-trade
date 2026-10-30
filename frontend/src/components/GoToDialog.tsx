import { useRef, useState } from 'react'
import { Modal } from './Modal'
import { fetchRealPrices, goto, previewGoto, syncRealPrices, type GotoResult, type OptionDataStatus, type RealPricesStatus } from '../lib/api'
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
  // Set while offering to sync to real prices.
  const [real, setReal] = useState<RealPricesStatus | 'loading' | null>(null)

  // Counts sync views opened, so a status that arrives after Back is ignored.
  const syncView = useRef(0)

  const openSync = async () => {
    const view = ++syncView.current
    setReal('loading')
    setError(null)
    try {
      const status = await fetchRealPrices()
      if (view === syncView.current) setReal(status)
    } catch (err) {
      if (view !== syncView.current) return
      setError(err instanceof Error ? err.message : String(err))
      setReal(null)
    }
  }

  const closeSync = () => {
    syncView.current++
    setReal(null)
  }

  const sync = async () => {
    setBusy(true)
    setError(null)
    try {
      await syncRealPrices()
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
      {real !== null ? (
        <SyncToReal status={real} busy={busy} onSync={sync} onBack={closeSync} />
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
            <button className="link-button" disabled={busy} onClick={openSync}>
              Sync to real prices…
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

interface SyncProps {
  status: RealPricesStatus | 'loading'
  busy: boolean
  onSync: () => void
  onBack: () => void
}

/** ", fetched at 2:41 PM" in the computer's own time (it's when, not market time). */
function fetchedAt(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso)
  return Number.isNaN(t.getTime()) ? '' : `, fetched at ${t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
}

/** How option prices' calibration to real options stands (it runs by itself once a day). */
function OptionSync({ status }: { status: OptionDataStatus | undefined }) {
  if (!status?.configured) return null
  const days =
    status.oldest && status.newest && status.oldest !== status.newest
      ? `${calendarDate(status.oldest)}–${calendarDate(status.newest)}`
      : status.newest
        ? calendarDate(status.newest)
        : ''
  return (
    <div className="option-sync">
      <p className="muted">
        {status.running
          ? `Option prices: fitting to real options, ${status.running.done} of ${status.running.total} symbols done…`
          : status.calibrated
            ? `Option prices: fitted to real options for ${status.calibrated} of ${status.total} symbols${days && ` (${days})`}, again daily. Syncing prices doesn't change them.`
            : 'Option prices: not fitted to real options yet; the server fetches them once a day.'}
        {!status.earnings && ' Real earnings dates need an Alpha Vantage key (ALPHAVANTAGE_API_KEY in backend/.env).'}
      </p>
      {status.last_error && <p className="warn-text">Option data: {status.last_error}</p>}
    </div>
  )
}

/** Offer to continue the market from now at the saved real prices. */
function SyncToReal({ status, busy, onSync, onBack }: SyncProps) {
  const back = (
    <button className="btn" disabled={busy} onClick={onBack}>
      Back
    </button>
  )
  if (status === 'loading') {
    return (
      <>
        <h2 id="goto-title">Sync to real prices</h2>
        <p className="muted">Checking real prices…</p>
        <div className="dialog-actions">{back}</div>
      </>
    )
  }
  if (!status.configured) {
    return (
      <>
        <h2 id="goto-title">Sync to real prices</h2>
        <p>
          Real prices come from Alpaca and need its free API keys (from a free paper trading account). Add the lines{' '}
          <code>APCA_API_KEY_ID=your-key-id</code> and <code>APCA_API_SECRET_KEY=your-secret</code> to{' '}
          <code>backend/.env</code> and restart the server.
        </p>
        <div className="dialog-actions">{back}</div>
      </>
    )
  }
  const problem = status.last_error && (
    <p className="warn-text">
      The last fetch failed: {status.last_error}
    </p>
  )
  if (status.available === 0) {
    return (
      <>
        <h2 id="goto-title">Sync to real prices</h2>
        <p>
          No real prices yet. The server fetches all {status.total} symbols from Alpaca when it starts and every{' '}
          {status.refresh_minutes ?? 15} minutes.
        </p>
        {problem}
        <OptionSync status={status.options} />
        <div className="dialog-actions">{back}</div>
      </>
    )
  }
  const missing = status.total - status.available
  const days =
    status.oldest && status.newest && status.oldest !== status.newest
      ? `${calendarDate(status.oldest)}–${calendarDate(status.newest)}`
      : status.newest
        ? calendarDate(status.newest)
        : ''
  return (
    <>
      <h2 id="goto-title">Sync to real prices?</h2>
      <p>
        Real prices are saved for {status.available} of {status.total} symbols{days && ` (prices from ${days}${fetchedAt(status.fetched)})`}. Prices
        move to them now{missing > 0 && `; the other ${missing} keep their current price`}.
      </p>
      <p className="muted">
        The market continues from here: its 5-year history is generated again to end at these prices, and going back
        before now starts a new market. Your cash, positions and orders are kept; positions are valued at the new
        prices, and working orders whose stop or limit the move crosses will trigger or fill at them.
      </p>
      {problem}
      <OptionSync status={status.options} />
      <div className="dialog-actions">
        <button className="btn primary" disabled={busy} onClick={onSync}>
          Sync prices
        </button>
        {back}
      </div>
    </>
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
