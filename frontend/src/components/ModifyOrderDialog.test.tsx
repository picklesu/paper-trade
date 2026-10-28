import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Order } from '../lib/types'
import { ModifyOrderDialog } from './ModifyOrderDialog'

const order: Order = {
  id: 3,
  symbol: 'SPY',
  side: 'buy',
  qty: 10,
  type: 'stop_limit',
  tif: 'day',
  limit_price: 571,
  stop_price: 570,
  created_at: '2026-01-05T15:00:00Z',
  status: 'working',
  triggered_at: null,
  filled_at: null,
  fill_price: null,
  closed_at: null,
  reason: null,
  replaces: null,
}

describe('ModifyOrderDialog', () => {
  it('sends the changes as a replace and closes', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...order, id: 4, replaces: 3 }) })
    vi.stubGlobal('fetch', fetchMock)
    const onClose = vi.fn()
    render(<ModifyOrderDialog order={order} onClose={onClose} />)
    expect(screen.getByRole('dialog')).toHaveTextContent('Modify order #3: Buy SPY, Stop limit 570.00 / 571.00')
    fireEvent.change(screen.getByLabelText('Limit price'), { target: { value: '572.5' } })
    fireEvent.change(screen.getByLabelText('Time in force'), { target: { value: 'gtc' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/orders/3')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ qty: 10, tif: 'gtc', limit_price: 572.5, stop_price: 570 })
  })

  it('shows server errors and stays open', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: 'Order #3 is already filled' }) }),
    )
    const onClose = vi.fn()
    render(<ModifyOrderDialog order={order} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('already filled')
    expect(onClose).not.toHaveBeenCalled()
  })
})
