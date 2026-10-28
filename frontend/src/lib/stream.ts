import { useEffect, useState } from 'react'
import type { Order, ProgressMessage, StreamMessage, TickMessage } from './types'

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

const RECONNECT_MS = 2000

function streamUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

/** Single WebSocket to the backend, reconnecting automatically when it drops. */
export function useStream() {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [tick, setTick] = useState<TickMessage | null>(null)
  const [progress, setProgress] = useState<ProgressMessage | null>(null)
  const [orders, setOrders] = useState<Order[]>([])

  useEffect(() => {
    let ws: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let closed = false

    const connect = () => {
      setStatus('connecting')
      ws = new WebSocket(streamUrl())
      ws.onopen = () => setStatus('connected')
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as StreamMessage
        if (msg.type === 'tick') {
          setTick(msg)
          setProgress(null)
        } else if (msg.type === 'progress') {
          setProgress(msg)
        } else if (msg.type === 'orders') {
          setOrders(msg.orders)
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

  return { status, tick, progress, orders }
}
