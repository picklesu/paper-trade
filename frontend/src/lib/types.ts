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
  /** The years of history before the market's start are still being generated. */
  building_history: boolean
}

export type Regime = 'calm' | 'normal' | 'volatile'

export interface Position {
  symbol: string
  qty: number
  avg_cost: number
  last: number
  market_value: number
  unrealized: number
  unrealized_pct: number
}

export interface Account {
  starting_cash: number
  cash: number
  buying_power: number
  net_liq: number
  day_pnl: number
  realized: number
  positions: Position[]
}

export type Side = 'buy' | 'sell'
export type OrderType = 'market' | 'limit' | 'stop' | 'stop_limit'
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
  /** The P&L a filled sell realized against the average cost; null for buys and unfilled orders. */
  realized: number | null
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
}

export interface TickMessage {
  type: 'tick'
  clock: ClockState
  regime: Regime
  quotes: Quote[]
  account: Account
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
