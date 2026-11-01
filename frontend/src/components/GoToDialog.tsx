import { useRef, useState } from 'react'
import { Modal } from './Modal'
import { fetchRealPrices, goto, previewGoto, triggerShock, updateRealHistory, type GotoResult, type OptionDataStatus, type RealPricesStatus } from '../lib/api'
import { calendarDate, calendarDateYear, dollars, marketDateTime, marketStamp, price, shiftInputDays, toMarketInput } from '../lib/format'
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
  // Set while showing the real data (status of the saved history and option fits).
  const [real, setReal] = useState<RealPricesStatus | 'loading' | null>(null)

  // Set while offering to trigger an uncertainty shock.
  const [shockView, setShockView] = useState(false)

  // Counts real-data views opened, so a status that arrives after Back is ignored.
  const dataView = useRef(0)

  const openData = async () => {
    const view = ++dataView.current
    setReal('loading')
    setError(null)
    try {
      const status = await fetchRealPrices()
      if (view === dataView.current) setReal(status)
    } catch (err) {
      if (view !== dataView.current) return
      setError(err instanceof Error ? err.message : String(err))
      setReal(null)
    }
  }

  const closeData = () => {
    dataView.current++
    setError(null)
    setReal(null)
  }

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
      {real !== null ? (
        <RealData status={real} onBack={closeData} />
      ) : shockView ? (
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
              This date is in the real past (the real daily prices run through {calendarDateYear(clock.real_through ?? '')}), where
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
              <button className="ctl" title={`The close of ${calendarDateYear(clock.real_through)}`} onClick={() => setValue(`${clock.real_through}T15:59`)}>
                Latest real day
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
            <button className="link-button" disabled={busy} onClick={openData}>
              Real data…
            </button>
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
            ? `Option prices: fitted to real options for ${status.calibrated} of ${status.total} symbols${days && ` (${days})`}, again daily.`
            : 'Option prices: not fitted to real options yet; the server fetches them once a day.'}
        {!status.earnings && ' Real earnings dates need an Alpha Vantage key (ALPHAVANTAGE_API_KEY in backend/.env).'}
      </p>
      {status.last_error && <p className="warn-text">Option data: {status.last_error}</p>}
    </div>
  )
}

/** Real daily history is fetched once; this fetches only the days since, when asked. */
function RealHistory({ status: initial }: { status: RealPricesStatus }) {
  const [status, setStatus] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const history = status.history
  const macro = status.macro
  if (!history?.configured && !macro?.configured) return null

  const update = async () => {
    setBusy(true)
    setFailed(false)
    setResult(null)
    try {
      const r = await updateRealHistory()
      setStatus(r.status)
      const days = Object.values(r.prices).reduce((a, b) => a + b, 0)
      const points = Object.values(r.macro).reduce((a, b) => a + b, 0)
      const added = days + points === 0 ? 'Already up to date.' : `Added ${days} days of prices (${Object.keys(r.prices).length} symbols) and ${points} economic readings.`
      setResult(r.problems.length ? `${added} Problems: ${r.problems.join('; ')}` : added)
      setFailed(r.problems.length > 0)
    } catch (err) {
      setResult(err instanceof Error ? err.message : String(err))
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="option-sync">
      <p>
        {history?.configured && history.newest ? `Real prices through ${calendarDateYear(history.newest)}` : 'Real prices: not downloaded yet'}
        {history?.configured && ` (${history.available} of ${history.total} symbols`}
        {macro?.configured && `${history?.configured ? '; ' : ' ('}${macro.available} of ${macro.total} economic series`}
        {')'}.
      </p>
      <p className="muted">History is downloaded once; Update adds only the days since.</p>
      <button className="btn" disabled={busy} onClick={update}>
        {busy ? 'Updating…' : 'Update real history'}
      </button>
      {result && <p className={failed ? 'warn-text' : 'muted'}>{result}</p>}
    </div>
  )
}

/** What real data is saved, how to bring in newer days, and how option prices stand. */
function RealData({ status, onBack }: { status: RealPricesStatus | 'loading'; onBack: () => void }) {
  const back = (
    <button className="btn" onClick={onBack}>
      Back
    </button>
  )
  if (status === 'loading') {
    return (
      <>
        <h2 id="goto-title">Real data</h2>
        <p className="muted">Checking real data…</p>
        <div className="dialog-actions">{back}</div>
      </>
    )
  }
  const hasHistory = !!status.history?.configured || !!status.macro?.configured
  return (
    <>
      <h2 id="goto-title">Real data</h2>
      {hasHistory ? (
        <RealHistory status={status} />
      ) : (
        <p>
          Real daily prices need free API keys in <code>backend/.env</code>: <code>TIINGO_API_KEY</code> for prices and{' '}
          <code>FRED_API_KEY</code> for economic series. Restart the server after adding them.
        </p>
      )}
      {status.last_error && <p className="warn-text">The last real quote fetch failed: {status.last_error}</p>}
      <OptionSync status={status.options} />
      <div className="dialog-actions">{back}</div>
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
