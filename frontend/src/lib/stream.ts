import { useEffect, useState } from 'react'

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

export interface Heartbeat {
  type: 'heartbeat'
  seq: number
  server_time: string
}

// Messages pushed by the backend. Later phases add quote, fill and account types.
export type StreamMessage = Heartbeat

const RECONNECT_MS = 2000

function streamUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

/** Single WebSocket to the backend, reconnecting automatically when it drops. */
export function useStream() {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [heartbeat, setHeartbeat] = useState<Heartbeat | null>(null)

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
        if (msg.type === 'heartbeat') setHeartbeat(msg)
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

  return { status, heartbeat }
}
