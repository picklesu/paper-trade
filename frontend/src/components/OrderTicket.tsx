import { type ReactNode, useEffect, useRef, useState } from 'react'
import { fetchExpirations, fetchOptionChain, placeOrder, previewOrder, type OrderRequest } from '../lib/api'
import { dollars, price } from '../lib/format'
import { contractLabel, expirationLabel, occSymbol, optionAction, parseOcc } from '../lib/options'
import type { Account, Expiration, OptionChain, Order, OrderPreview, OrderType, Quote, Side, TimeInForce } from '../lib/types'
import { OrderReview } from './OrderReview'
import { OrderStatusLine } from './OrderStatusLine'
import { TifSelect } from './TifSelect'

/** A request to fill the ticket with an option order (from the chain). */
export interface TicketFill {
  symbol: string
  /** null (a mid click): keep the side chosen. */
  side: Side | null
  limit: number
  id: number
}

interface Props {
  quote: Quote
  account: Account
  orders: Order[]
  /** The streamed option chain, used for live contract quotes when it's the one picked. */
  chain?: OptionChain | null
  /** Fill the option form (a click on a chain bid or ask). */
  fill?: TicketFill | null
}

const TYPES: { value: OrderType; label: string }[] = [
  { value: 'market', label: 'Market' },
  { value: 'limit', label: 'Limit' },
  { value: 'stop', label: 'Stop' },
  { value: 'stop_limit', label: 'Stop limit' },
]

/** Order entry for stocks or options: fill in the form, review the server's preview, then place. */
export function OrderTicket({ quote, account, orders, chain = null, fill = null }: Props) {
  const [mode, setMode] = useState<'stock' | 'option'>(fill ? 'option' : 'stock')
  const flow = useOrderFlow(orders)
  // A chain click switches to options (the option form reads the fill itself).
  const [lastFill, setLastFill] = useState(fill?.id)
  if (fill && fill.id !== lastFill) {
    setLastFill(fill.id)
    setMode('option')
    flow.edit()
  }
  // The option form mounts the first time it's shown (it fetches expirations and chains).
  const [optionsUsed, setOptionsUsed] = useState(mode === 'option')
  if (mode === 'option' && !optionsUsed) setOptionsUsed(true)
  // A review or error is about the symbol it was made for.
  const [lastSymbol, setLastSymbol] = useState(quote.symbol)
  if (quote.symbol !== lastSymbol) {
    setLastSymbol(quote.symbol)
    flow.edit()
  }

  if (flow.review) return flow.reviewView
  return (
    <div className="ticket-form">
      <div className="mode-toggle" role="group" aria-label="Trade">
        {(['stock', 'option'] as const).map((m) => (
          <button key={m} type="button" className={mode === m ? 'ctl active' : 'ctl'} aria-pressed={mode === m} onClick={() => setMode(m)}>
            {m === 'stock' ? 'Stock' : 'Options'}
          </button>
        ))}
      </div>
      {/* Each form stays mounted once used, so switching keeps what's filled in (and a used chain click isn't reapplied). */}
      <div hidden={mode !== 'stock'}>
        <StockForm quote={quote} account={account} flow={flow} />
      </div>
      {optionsUsed && (
        <div hidden={mode !== 'option'}>
          <OptionForm quote={quote} account={account} flow={flow} chain={chain} fill={fill} />
        </div>
      )}
      {flow.error && (
        <p className="ticket-result error" role="alert">
          {flow.error}
        </p>
      )}
      {flow.placed && !flow.error && <OrderStatusLine order={flow.placed} orders={orders} />}
    </div>
  )
}

interface Flow {
  busy: boolean
  error: string | null
  review: OrderPreview | null
  placed: Order | null
  reviewView: ReactNode
  reviewOrder: (request: OrderRequest) => void
  edit: () => void
}

/** Review, then place: shared by the stock and option forms. */
function useOrderFlow(orders: Order[]): Flow {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [review, setReview] = useState<{ preview: OrderPreview; request: OrderRequest } | null>(null)
  const [placed, setPlaced] = useState<Order | null>(null)
  const [placedSeen, setPlacedSeen] = useState(false)

  // Once the stream has shown the placed order, its disappearing means going back in
  // time erased it: drop the line rather than show a stale or reused order.
  const inStream = placed !== null && orders.some((o) => o.id === placed.id)
  if (inStream && !placedSeen) setPlacedSeen(true)
  if (placed && placedSeen && !inStream) {
    setPlaced(null)
    setPlacedSeen(false)
  }

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  const place = () =>
    run(async () => {
      const order = await placeOrder(review!.request)
      setPlaced(order)
      setPlacedSeen(false)
      setReview(null)
    })
  const reviewView = review && (
    <div className="ticket-form">
      <OrderReview preview={review.preview} busy={busy} onPlace={place} onEdit={() => setReview(null)} />
      {error && (
        <p className="ticket-result error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
  return {
    busy,
    error,
    review: review?.preview ?? null,
    placed,
    reviewView,
    reviewOrder: (request) => run(async () => setReview({ preview: await previewOrder(request), request })),
    edit: () => {
      setReview(null)
      setError(null)
    },
  }
}

function SideToggle({ side, onSide, labels }: { side: Side; onSide: (s: Side) => void; labels?: Record<Side, string> }) {
  return (
    <div className="side-toggle" role="group" aria-label="Side">
      {(['buy', 'sell'] as const).map((s) => (
        <button key={s} type="button" className={side === s ? `side ${s} active` : `side ${s}`} aria-pressed={side === s} onClick={() => onSide(s)}>
          {labels?.[s] ?? (s === 'buy' ? 'Buy' : 'Sell')}
        </button>
      ))}
    </div>
  )
}

function StockForm({ quote, account, flow }: { quote: Quote; account: Account; flow: Flow }) {
  const [side, setSide] = useState<Side>('buy')
  const [qty, setQty] = useState('10')
  const [type, setType] = useState<OrderType>('market')
  const [limit, setLimit] = useState('')
  const [stop, setStop] = useState('')
  const [tif, setTif] = useState<TimeInForce>('day')

  // Start price fields at the current price when the symbol or order type changes,
  // but not on every tick (that would overwrite what the user is typing).
  const lastRef = useRef(quote.last)
  useEffect(() => {
    lastRef.current = quote.last
  })
  useEffect(() => {
    setLimit(lastRef.current.toFixed(2))
    setStop(lastRef.current.toFixed(2))
  }, [quote.symbol, type])

  const needsLimit = type === 'limit' || type === 'stop_limit'
  const needsStop = type === 'stop' || type === 'stop_limit'
  const shares = Number(qty)
  const owned = account.positions.find((p) => p.symbol === quote.symbol)?.qty ?? 0

  const estPrice = needsLimit ? Number(limit) : needsStop ? Number(stop) : side === 'buy' ? quote.ask : quote.bid
  const estTotal = Number.isFinite(shares * estPrice) ? shares * estPrice : 0

  return (
    <form
      className="ticket-fields"
      onSubmit={(e) => {
        e.preventDefault()
        flow.reviewOrder({
          symbol: quote.symbol,
          side,
          qty: shares,
          type,
          tif: type === 'market' ? 'day' : tif,
          limit_price: needsLimit ? Number(limit) : undefined,
          stop_price: needsStop ? Number(stop) : undefined,
        })
      }}
    >
      <div className="ticket-quote">
        <span className="sym">{quote.symbol}</span>
        <span className="muted">
          Bid {price(quote.bid)} · Ask {price(quote.ask)}
        </span>
      </div>

      <SideToggle side={side} onSide={setSide} />

      <label className="field">
        <span>Quantity (shares)</span>
        <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
      </label>

      <label className="field">
        <span>Order type</span>
        <select value={type} onChange={(e) => setType(e.target.value as OrderType)}>
          {TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </label>

      {needsStop && (
        <label className="field">
          <span>Stop price</span>
          <input type="number" min={0.01} step={0.01} value={stop} onChange={(e) => setStop(e.target.value)} />
        </label>
      )}
      {needsLimit && (
        <label className="field">
          <span>Limit price</span>
          <input type="number" min={0.01} step={0.01} value={limit} onChange={(e) => setLimit(e.target.value)} />
        </label>
      )}
      {type !== 'market' && <TifSelect value={tif} onChange={setTif} />}

      <dl className="ticket-summary">
        <div>
          <dt>Estimated {side === 'buy' ? 'cost' : 'proceeds'}</dt>
          <dd>{dollars(estTotal)}</dd>
        </div>
        <div>
          <dt>{side === 'buy' ? 'Buying power' : 'Shares owned'}</dt>
          <dd>{side === 'buy' ? dollars(account.buying_power) : owned.toLocaleString()}</dd>
        </div>
      </dl>

      <button type="submit" className={`btn submit ${side}`} disabled={flow.busy || !(shares > 0)}>
        Review {side === 'buy' ? 'buy' : 'sell'} {shares > 0 ? shares.toLocaleString() : ''} {quote.symbol}
      </button>
    </form>
  )
}

interface OptionFormProps {
  quote: Quote
  account: Account
  flow: Flow
  chain: OptionChain | null
  fill: TicketFill | null
}

function OptionForm({ quote, account, flow, chain, fill }: OptionFormProps) {
  const underlying = quote.symbol
  const fromFill = fill ? parseOcc(fill.symbol) : null
  const [expiration, setExpiration] = useState<string | null>(fromFill?.expiration ?? null)
  const [right, setRight] = useState<'call' | 'put'>(fromFill?.right ?? 'call')
  const [strike, setStrike] = useState<number | null>(fromFill?.strike ?? null)
  const [side, setSide] = useState<Side>(fill?.side ?? 'buy')
  const [qty, setQty] = useState('1')
  const [type, setType] = useState<'market' | 'limit'>(fill ? 'limit' : 'market')
  const [limit, setLimit] = useState(fill ? fill.limit.toFixed(2) : '')
  const [tif, setTif] = useState<TimeInForce>('day')

  // A new fill (another chain click) replaces the form's contract, side and price.
  const [seenFill, setSeenFill] = useState(fill?.id)
  if (fill && fill.id !== seenFill) {
    setSeenFill(fill.id)
    const c = parseOcc(fill.symbol)
    if (c) {
      setExpiration(c.expiration)
      setRight(c.right)
      setStrike(c.strike)
    }
    if (fill.side) setSide(fill.side)
    setType('limit')
    setLimit(fill.limit.toFixed(2))
  }

  const expirations = useExpirationList(underlying)
  const listed = expirations.find((e) => e.date === expiration) ? expiration : (expirations.find((e) => e.days >= 7) ?? expirations[0])?.date ?? null
  const contractChain = useContractChain(underlying, listed, chain)
  const strikes = contractChain?.strikes ?? []
  const nearest = strikes.length ? strikes.reduce((a, b) => (Math.abs(b - quote.last) < Math.abs(a - quote.last) ? b : a)) : null
  const k = strike !== null && strikes.includes(strike) ? strike : nearest
  const i = k === null ? -1 : strikes.indexOf(k)
  const bid = i >= 0 ? contractChain![right].bid[i] : null
  const ask = i >= 0 ? contractChain![right].ask[i] : null
  const symbol = listed && k !== null ? occSymbol(underlying, listed, right, k) : null
  const held = account.positions.find((p) => p.symbol === symbol)?.qty ?? 0
  const contracts = Number(qty)
  const est = type === 'limit' ? Number(limit) : side === 'buy' ? ask : bid
  const estTotal = est !== null && Number.isFinite(contracts * est) ? contracts * est * 100 : 0
  const action = optionAction(side, held, right)
  const label = listed && k !== null ? contractLabel(underlying, listed, right, k) : ''

  return (
    <form
      className="ticket-fields"
      onSubmit={(e) => {
        e.preventDefault()
        if (!symbol) return
        flow.reviewOrder({
          symbol,
          side,
          qty: contracts,
          type,
          tif: type === 'market' ? 'day' : tif,
          limit_price: type === 'limit' ? Number(limit) : undefined,
        })
      }}
    >
      <div className="ticket-quote">
        <span className="sym">{underlying}</span>
        <span className="muted">{bid !== null && ask !== null ? `Bid ${price(bid)} · Ask ${price(ask)}` : 'Loading…'}</span>
      </div>

      <div className="option-contract">
        <label className="field">
          <span>Expiration</span>
          <select value={listed ?? ''} onChange={(e) => setExpiration(e.target.value)}>
            {expirations.map((e) => (
              <option key={e.date} value={e.date}>
                {expirationLabel(e)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Strike</span>
          <select value={k ?? ''} onChange={(e) => setStrike(Number(e.target.value))}>
            {strikes.map((s) => (
              <option key={s} value={s}>
                {price(s)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mode-toggle" role="group" aria-label="Call or put">
        {(['call', 'put'] as const).map((r) => (
          <button key={r} type="button" className={right === r ? 'ctl active' : 'ctl'} aria-pressed={right === r} onClick={() => setRight(r)}>
            {r === 'call' ? 'Call' : 'Put'}
          </button>
        ))}
      </div>

      <SideToggle side={side} onSide={setSide} />
      <p className="muted small option-action">
        {action}
        {held !== 0 && ` · you hold ${held > 0 ? held : `${-held} short`}`}
      </p>

      <label className="field">
        <span>Quantity (contracts of 100 shares)</span>
        <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
      </label>
      <label className="field">
        <span>Order type</span>
        <select value={type} onChange={(e) => setType(e.target.value as 'market' | 'limit')}>
          <option value="market">Market</option>
          <option value="limit">Limit</option>
        </select>
      </label>
      {type === 'limit' && (
        <label className="field">
          <span>Limit price (per share)</span>
          <input type="number" min={0.01} step={0.01} value={limit} onChange={(e) => setLimit(e.target.value)} />
        </label>
      )}
      {type !== 'market' && <TifSelect value={tif} onChange={setTif} />}

      <dl className="ticket-summary">
        <div>
          <dt>Estimated {side === 'buy' ? 'cost' : 'credit'}</dt>
          <dd>{dollars(estTotal)}</dd>
        </div>
        <div>
          <dt>Buying power</dt>
          <dd>{dollars(account.buying_power)}</dd>
        </div>
      </dl>

      <button type="submit" className={`btn submit ${side}`} disabled={flow.busy || !(contracts > 0) || !symbol}>
        Review {action.split(' (')[0].toLowerCase()} {contracts > 0 ? contracts.toLocaleString() : ''} {label}
      </button>
    </form>
  )
}

/** The underlying's expirations (fetched when it changes). */
function useExpirationList(symbol: string): Expiration[] {
  const [loaded, setLoaded] = useState<{ symbol: string; list: Expiration[] }>({ symbol: '', list: [] })
  useEffect(() => {
    let live = true
    fetchExpirations(symbol)
      .then((list) => live && setLoaded({ symbol, list }))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [symbol])
  return loaded.symbol === symbol ? loaded.list : []
}

/** The chain the contract is picked from: the streamed one when it's this
 *  expiration (live), else fetched every few seconds. */
function useContractChain(symbol: string, expiration: string | null, streamed: OptionChain | null): OptionChain | null {
  const live = streamed && streamed.symbol === symbol && streamed.expiration === expiration ? streamed : null
  const isLive = live !== null
  const [fetched, setFetched] = useState<OptionChain | null>(null)
  useEffect(() => {
    if (isLive || !expiration) return
    let on = true
    const load = () =>
      fetchOptionChain(symbol, expiration)
        .then((c) => on && setFetched(c))
        .catch(() => {})
    load()
    const timer = setInterval(load, 3000)
    return () => {
      on = false
      clearInterval(timer)
    }
  }, [symbol, expiration, isLive])
  if (live) return live
  return fetched && fetched.symbol === symbol && fetched.expiration === expiration ? fetched : null
}
