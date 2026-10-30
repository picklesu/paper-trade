export interface Quote {
  symbol: string
  name: string
  last: number
  bid: number
  ask: number
  open: number
  high: number
  low: number
  prev_close: number
  change: number
  change_pct: number
  volume: number
  /** Shares shown at the bid and ask (simulated, for show). */
  bid_size: number
  ask_size: number
}

/** 52-week range and average daily volume, from finished sessions (not today). */
export interface QuoteStats {
  high_52w: number
  low_52w: number
  avg_volume: number
}

export interface SymbolInfo {
  symbol: string
  name: string
  sector: string | null
}

export interface Watchlist {
  id: number
  name: string
  /** In the user's order. */
  symbols: string[]
}

export interface ClockState {
  time: string
  speed: number
  paused: boolean
  speeds: number[]
  /** Earliest market time with price history; going before it starts a new market. */
  origin: string
  /** Today's 9:30 AM open, for aligning chart bars. */
  session_open: string
  /** Changes whenever market time jumps or history changes: charts reload. */
  timeline: number
  /** Changes when an option calibration changes: expirations load again. */
  calibration?: number
  /** The years of history before the market's start are still being generated. */
  building_history: boolean
}

export type Regime = 'calm' | 'normal' | 'volatile'

/** An option contract, as the server describes it. */
export interface OptionInfo {
  underlying: string
  /** "YYYY-MM-DD" */
  expiration: string
  right: 'call' | 'put'
  strike: number
  /** "AAPL Nov 20 '26 230 Call" */
  label: string
}

export interface Position {
  /** A stock's symbol, or an option's OCC symbol (see `option`). */
  symbol: string
  /** Shares, or contracts (negative: short). */
  qty: number
  /** Per share (an option's price per share; a contract is 100). */
  avg_cost: number
  /** The last price, or an option's mark (between bid and ask). */
  last: number
  market_value: number
  unrealized: number
  unrealized_pct: number
  /** Share-equivalent delta and dollars a day of theta (older servers send neither). */
  delta?: number
  theta?: number
  option?: OptionInfo
  bid?: number
  ask?: number
}

export interface Account {
  starting_cash: number
  cash: number
  buying_power: number
  /** Cash securing short puts. */
  collateral?: number
  net_liq: number
  day_pnl: number
  realized: number
  positions: Position[]
}

export type Side = 'buy' | 'sell'
/** Exercise, assignment and expiration are records of what happened to options, not orders placed. */
export type OrderType = 'market' | 'limit' | 'stop' | 'stop_limit' | 'exercise' | 'assignment' | 'expiration'
export type TimeInForce = 'day' | 'gtc'
export type OrderStatus = 'working' | 'filled' | 'canceled' | 'expired' | 'rejected'

export interface Order {
  id: number
  symbol: string
  side: Side
  qty: number
  type: OrderType
  tif: TimeInForce
  limit_price: number | null
  stop_price: number | null
  created_at: string
  status: OrderStatus
  triggered_at: string | null
  filled_at: string | null
  fill_price: number | null
  closed_at: string | null
  reason: string | null
  /** The order this one replaced (cancel/replace). */
  replaces: number | null
  /** The P&L a closing fill realized against the average cost; null for opening ones and unfilled orders. */
  realized: number | null
  /** Options: whether it opens or closes a position, and the contract. */
  effect?: 'open' | 'close' | null
  option?: OptionInfo
}

/** What placing an order would do, from POST /api/orders/preview. */
export interface OrderPreview {
  symbol: string
  side: Side
  qty: number
  type: OrderType
  tif: TimeInForce
  limit_price: number | null
  stop_price: number | null
  fills_now: boolean
  estimated_price: number
  estimated_total: number
  commission: number
  buying_power_before: number
  buying_power_after: number
  cash_before: number
  cash_after: number
  position_before: number
  position_after: number
  avg_cost_after: number
  estimated_realized: number | null
  warnings: string[]
  /** Options only. */
  option?: OptionInfo
  effect?: 'open' | 'close'
  breakeven?: number | null
  max_loss?: number | null
  max_gain?: number | null
  /** Cash a put sold to open sets aside. */
  collateral?: number
  /** Shares a call sold to open locks. */
  shares_locked?: number
}

/** What exercising long contracts now would do. */
export interface ExercisePreview {
  symbol: string
  option: OptionInfo
  qty: number
  /** Shares received (call) or delivered (negative, put). */
  shares: number
  strike: number
  cash_change: number
  cash_before: number
  cash_after: number
  intrinsic: number
  bid: number
  warnings: string[]
}

export interface Expiration {
  /** "YYYY-MM-DD"; options expire at that day's 4:00 PM close. */
  date: string
  /** Quarterly: a month- or quarter-end expiration. */
  kind: 'daily' | 'weekly' | 'quarterly' | 'monthly' | 'leaps'
  /** Calendar days from today. */
  days: number
}

/** One side of a chain: a value per strike, in the chain's strike order. */
export interface ChainSide {
  bid: number[]
  ask: number[]
  /** Contracts shown at the bid and ask. */
  bid_size: number[]
  ask_size: number[]
  mark: number[]
  iv: number[]
  delta: number[]
  gamma: number[]
  /** Per calendar day. */
  theta: number[]
  /** Per 1 point of implied volatility. */
  vega: number[]
  /** Per 1 point of the interest rate. */
  rho: number[]
  volume: number[]
  oi: number[]
}

/** What real option prices say about the symbol (fitted once a day). */
export interface ChainCalibration {
  /** The trading day of the real options the shape was fitted to; null: built-in estimates. */
  as_of: string | null
  /** The fitted skew (SSVI rho) at 30 days; null without a fit. */
  skew: number | null
  /** Implied over realized volatility at 30 days (applied to the simulated volatility). */
  premium: number
  /** The real next report and its implied move (null if not measurable). */
  earnings: { day: string; timing: string; move: number | null } | null
  /** Median fit error, in volatility points. */
  fit_error: number | null
}

export interface OptionChain {
  symbol: string
  expiration: string
  kind: Expiration['kind']
  days: number
  years: number
  spot: number
  rate: number
  atm_iv: number
  /** One standard deviation of the price at expiration, in dollars. */
  expected_move: number
  /** An earnings report falls before expiration (implied volatility is higher until it passes). */
  earnings: boolean
  calibration: ChainCalibration
  strikes: number[]
  call: ChainSide
  put: ChainSide
}

export interface TickMessage {
  type: 'tick'
  clock: ClockState
  regime: Regime
  quotes: Quote[]
  account: Account
  /** The option chain this client subscribed to (null: no such expiration; absent: none asked for). */
  chain?: OptionChain | null
}

/** Every order, newest first; sent on connect and whenever an order changes. */
export interface OrdersMessage {
  type: 'orders'
  orders: Order[]
}

/** Sent while fast-forwarding, every few sessions generated. */
export interface ProgressMessage {
  type: 'progress'
  done: number
  total: number
}

/** Sent while building chart history in the background. */
export interface HistoryMessage {
  type: 'history'
  done: number
  total: number
}

/** Every watchlist; sent on connect and whenever one changes. */
export interface WatchlistsMessage {
  type: 'watchlists'
  watchlists: Watchlist[]
}

// Messages pushed by the backend.
export type StreamMessage = TickMessage | ProgressMessage | OrdersMessage | HistoryMessage | WatchlistsMessage

export type GotoAction = 'forward' | 'rewind' | 'reset' | 'none'
