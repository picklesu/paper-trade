import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ClockState } from '../lib/types'
import { GoToDialog } from './GoToDialog'

const clock: ClockState = {
  time: '2026-01-07T15:30:00Z', // Wed 10:30 ET
  speed: 1,
  paused: false,
  speeds: [1, 10, 60, 600],
  origin: '2026-01-05T14:30:00Z',
  session_open: '2026-01-07T14:30:00Z',
  timeline: 0,
  building_history: false,
}

/** fetch stub: GET preview returns `action`; POST goto succeeds. */
function mockFetch(action: string, target = '2026-01-06T15:30:00Z') {
  const fn = vi.fn(async (_url: string, init?: RequestInit) => ({
    ok: true,
    json: async () => (init?.method === 'POST' ? { action, target, clock } : { action, target }),
  }))
  vi.stubGlobal('fetch', fn)
  return fn
}

function postedBody(fetchMock: ReturnType<typeof mockFetch>) {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  return call ? JSON.parse(call[1]!.body as string) : null
}

describe('GoToDialog', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts at the current market time in New York', () => {
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    expect(screen.getByLabelText('Market date and time')).toHaveValue('2026-01-07T10:30')
  })

  it('shortcuts shift the date', () => {
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '+1 week' }))
    expect(screen.getByLabelText('Market date and time')).toHaveValue('2026-01-14T10:30')
  })

  it('goes forward without asking', async () => {
    const fetchMock = mockFetch('forward', '2026-01-14T15:30:00Z')
    const onClose = vi.fn()
    render(<GoToDialog clock={clock} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: '+1 week' }))
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(postedBody(fetchMock)).toEqual({ time: '2026-01-14T10:30', future: 'replay' })
  })

  it('going back asks to replay or generate a new future', async () => {
    const fetchMock = mockFetch('rewind')
    const onClose = vi.fn()
    render(<GoToDialog clock={clock} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: '−1 day' }))
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))

    expect(await screen.findByText(/Go back to Tue, Jan 6, 2026, 10:30 AM ET/)).toBeInTheDocument()
    expect(screen.getByText(/nothing is erased/)).toBeInTheDocument()
    expect(postedBody(fetchMock)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Generate a new future' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(postedBody(fetchMock)).toEqual({ time: '2026-01-06T10:30', future: 'new' })
  })

  it('going back lists the trades it will erase and how positions are valued', async () => {
    const fn = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        action: 'rewind',
        target: '2026-01-06T15:30:00Z',
        erased_fills: [
          { id: 7, symbol: 'MSFT', side: 'buy', qty: 20, fill_price: 421.5, filled_at: '2026-01-06T18:00:00Z' },
        ],
        erased_orders: 2,
        cash_now: 91_570,
        cash_then: 100_000,
        positions: [{ symbol: 'AAPL', qty: 10, price_now: 231.2, price_then: 229.8 }],
      }),
    }))
    vi.stubGlobal('fetch', fn)
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '−1 day' }))
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))

    expect(await screen.findByText(/This trade will be erased and refunded/)).toBeInTheDocument()
    expect(screen.getByText(/Bought 20 MSFT at 421.50/)).toBeInTheDocument()
    expect(screen.getByText('Cash goes from $91,570.00 to $100,000.00.')).toBeInTheDocument()
    expect(screen.getByText('229.80')).toBeInTheDocument()
  })

  it('replay sends future=replay', async () => {
    const fetchMock = mockFetch('rewind')
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '−1 day' }))
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Replay the same prices' }))
    await waitFor(() => expect(postedBody(fetchMock)).toEqual({ time: '2026-01-06T10:30', future: 'replay' }))
  })

  it('before the market history warns about starting over', async () => {
    const fetchMock = mockFetch('reset', '2025-06-02T13:30:00Z')
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.change(screen.getByLabelText('Market date and time'), { target: { value: '2025-06-02T09:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))

    expect(await screen.findByText(/Start a new market on/)).toBeInTheDocument()
    expect(screen.getByText(/all price history and trades are erased/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Start new market' }))
    await waitFor(() => expect(postedBody(fetchMock)).toEqual({ time: '2025-06-02T09:30', future: 'replay' }))
  })

  it('Go with the time unchanged just closes', () => {
    const fetchMock = mockFetch('rewind')
    const onClose = vi.fn()
    render(<GoToDialog clock={clock} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))
    expect(onClose).toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('disables Go while checking, so a double click jumps once', async () => {
    const fetchMock = mockFetch('forward', '2026-01-14T15:30:00Z')
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '+1 week' }))
    const go = screen.getByRole('button', { name: 'Go' })
    fireEvent.click(go)
    fireEvent.click(go)
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(1))
    expect(fetchMock.mock.calls.filter(([, i]) => i?.method !== 'POST')).toHaveLength(1)
  })

  it('cancel closes without jumping', async () => {
    const fetchMock = mockFetch('rewind')
    const onClose = vi.fn()
    render(<GoToDialog clock={clock} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: '−1 day' }))
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))
    await screen.findByText(/Go back to/)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
    expect(postedBody(fetchMock)).toBeNull()
  })

  it('shows server errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? { ok: false, status: 422, json: async () => ({ detail: 'can jump at most 1260 trading days ahead' }) }
          : { ok: true, json: async () => ({ action: 'forward', target: '2040-01-02T14:30:00Z' }) },
      ),
    )
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.change(screen.getByLabelText('Market date and time'), { target: { value: '2040-01-02T09:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at most 1260 trading days')
  })
})

describe('GoToDialog: sync to real prices', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  function mockReal(status: object) {
    const fn = vi.fn(async (url: string, init?: RequestInit) => ({
      ok: true,
      json: async () => (url === '/api/real-prices/sync' && init?.method === 'POST' ? { ...status, clock } : status),
    }))
    vi.stubGlobal('fetch', fn)
    return fn
  }

  it('says how many symbols have real prices, then syncs', async () => {
    const fetched = '2026-10-29T14:05:00Z'
    const fetchMock = mockReal({ configured: true, available: 25, total: 40, oldest: '2026-10-27', newest: '2026-10-28', fetched })
    const onClose = vi.fn()
    render(<GoToDialog clock={clock} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sync to real prices…' }))

    const time = new Date(fetched).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    expect(await screen.findByText(`saved for 25 of 40 symbols (prices from Oct 27–Oct 28, fetched at ${time})`, { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/the other 15 keep their current price/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Sync prices' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/real-prices/sync' && init?.method === 'POST')).toBe(true)
  })

  it('shows the last fetch problem', async () => {
    mockReal({ configured: true, available: 0, total: 40, last_error: 'Alpaca rejected the API keys (401).' })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sync to real prices…' }))
    expect(await screen.findByText(/The last fetch failed: Alpaca rejected the API keys/)).toBeInTheDocument()
  })

  it('ignores a status that arrives after Back', async () => {
    let answer: (v: unknown) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((resolve) => (answer = resolve))),
    )
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sync to real prices…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    answer({ ok: true, json: async () => ({ configured: true, available: 5, total: 40 }) })
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.getByLabelText('Market date and time')).toBeInTheDocument()
    expect(screen.queryByText(/Sync to real prices\?/)).not.toBeInTheDocument()
  })

  it('explains when there is no key or nothing fetched yet', async () => {
    mockReal({ configured: false, available: 0, total: 40 })
    const { unmount } = render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sync to real prices…' }))
    expect(await screen.findByText(/need its free API keys/)).toBeInTheDocument()
    unmount()

    mockReal({ configured: true, available: 0, total: 40, refresh_minutes: 15 })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sync to real prices…' }))
    expect(await screen.findByText(/No real prices yet.*every 15 minutes/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sync prices' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByLabelText('Market date and time')).toBeInTheDocument()
  })
})
