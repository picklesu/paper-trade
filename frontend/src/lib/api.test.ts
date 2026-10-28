import { describe, expect, it, vi } from 'vitest'
import { previewOrder } from './api'

describe('api errors', () => {
  it('turns validation error lists into readable text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: [{ loc: ['body', 'qty'], msg: 'Input should be a valid integer' }] }),
      }),
    )
    await expect(previewOrder({ symbol: 'SPY', side: 'buy', qty: 1.5, type: 'market', tif: 'day' })).rejects.toThrow(
      'qty: Input should be a valid integer',
    )
  })

  it('passes plain messages through', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: 'Nope' }) }))
    await expect(previewOrder({ symbol: 'SPY', side: 'buy', qty: 1, type: 'market', tif: 'day' })).rejects.toThrow('Nope')
  })
})
