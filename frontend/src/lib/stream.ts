import { useCallback, useEffect, useRef, useState } from 'react'
import type { HistoryMessage, OptionChain, Order, ProgressMessage, Quote, StreamMessage, TickMessage, Watchlist } from './types'

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

const RECONNECT_MS = 2000

function streamUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

/** Every quote seen so far, by symbol. A quote whose values didn't change keeps
 *  the same object, so rows showing it can skip re-rendering. */
export type QuoteMap = ReadonlyMap<string, Quote>

function sameQuote(a: Quote, b: Quote): boolean {
  for (const key of Object.keys(b) as (keyof Quote)[]) if (a[key] !== b[key]) return false
  return true
}

export function mergeQuotes(prev: QuoteMap, incoming: Quote[], fresh = false): QuoteMap {
  // `fresh`: time jumped, so quotes not in this tick are from another timeline.
  const next = fresh ? new Map<string, Quote>() : new Map(prev)
  for (const q of incoming) {
    const old = prev.get(q.symbol)
    next.set(q.symbol, old && sameQuote(old, q) ? old : q)
  }
  return next
}

/** The option chain a client shows: its ticks then carry it. */
export interface ChainRequest {
  symbol: string
  expiration: string
}

/** Single WebSocket to the backend, reconnecting automatically when it drops.
 *  `subscribe(symbols, chain)` asks for only those symbols' quotes and that option
 *  chain (sent again after a reconnect); until it's called, every symbol's quotes
 *  arrive and no chain. */
export function useStream() {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [tick, setTick] = useState<TickMessage | null>(null)
  const [quotes, setQuotes] = useState<QuoteMap>(() => new Map())
  const [progress, setProgress] = useState<ProgressMessage | null>(null)
  const [orders, setOrders] = useState<Order[]>([])
  const [history, setHistory] = useState<HistoryMessage | null>(null)
  const [watchlists, setWatchlists] = useState<Watchlist[] | null>(null)
  const [chain, setChain] = useState<OptionChain | null>(null)
  const socket = useRef<WebSocket | null>(null)
  const wanted = useRef<{ symbols: string[]; chain: ChainRequest | null } | null>(null)
  const timeline = useRef<number | null>(null)

  const sendSubscription = (ws: WebSocket | null) => {
    if (ws && wanted.current && ws.readyState === 1) ws.send(JSON.stringify({ type: 'subscribe', ...wanted.current }))
  }

  useEffect(() => {
    let ws: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let closed = false

    const connect = () => {
      setStatus('connecting')
      ws = new WebSocket(streamUrl())
      socket.current = ws
      ws.onopen = () => {
        setStatus('connected')
        sendSubscription(ws)
      }
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as StreamMessage
        if (msg.type === 'tick') {
          setTick(msg)
          const jumped = timeline.current !== null && timeline.current !== msg.clock.timeline
          timeline.current = msg.clock.timeline
          setQuotes((prev) => mergeQuotes(prev, msg.quotes, jumped))
          setChain(msg.chain ?? null)
          setProgress(null)
        } else if (msg.type === 'progress') {
          setProgress(msg)
        } else if (msg.type === 'orders') {
          setOrders(msg.orders)
        } else if (msg.type === 'history') {
          setHistory(msg.done < msg.total ? msg : null)
        } else if (msg.type === 'watchlists') {
          setWatchlists(msg.watchlists)
        }
      }
      ws.onclose = () => {
        if (closed) return
        setStatus('disconnected')
        retry = setTimeout(connect, RECONNECT_MS)
      }
    }

    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      ws?.close()
    }
  }, [])

  const subscribe = useCallback((symbols: string[], chain: ChainRequest | null = null) => {
    const next = { symbols, chain }
    if (wanted.current && JSON.stringify(wanted.current) === JSON.stringify(next)) return
    wanted.current = next
    sendSubscription(socket.current)
  }, [])

  return { status, tick, quotes, progress, orders, history, watchlists, chain, subscribe }
}
