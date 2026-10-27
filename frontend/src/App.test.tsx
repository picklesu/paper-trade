import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

// Minimal stand-in so tests don't need a running backend.
class FakeWebSocket {
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  close() {}
}
vi.stubGlobal('WebSocket', FakeWebSocket)

describe('App shell', () => {
  it('renders the main panels', () => {
    render(<App />)
    for (const name of ['Watchlist', 'Chart', 'Order Entry']) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('status')).toHaveTextContent('Connecting')
  })

  it('switches bottom tabs', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: 'Orders' }))
    expect(screen.getByRole('tab', { name: 'Orders' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('No orders yet')
  })
})
