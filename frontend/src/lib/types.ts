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
}

export interface ClockState {
  time: string
  speed: number
  paused: boolean
  speeds: number[]
  /** Earliest market time with price history; going before it starts a new market. */
  origin: string
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

// Messages pushed by the backend.
export type StreamMessage = TickMessage | ProgressMessage | OrdersMessage

export type GotoAction = 'forward' | 'rewind' | 'reset' | 'none'
