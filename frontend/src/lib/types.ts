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
}

export type Regime = 'calm' | 'normal' | 'volatile'

export interface TickMessage {
  type: 'tick'
  clock: ClockState
  regime: Regime
  quotes: Quote[]
}

// Messages pushed by the backend. Later phases add fill and account types.
export type StreamMessage = TickMessage
