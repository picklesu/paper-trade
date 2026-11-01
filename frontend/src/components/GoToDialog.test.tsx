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

  it('triggers an uncertainty shock after explaining it', async () => {
    const fetchMock = mockFetch('none')
    const onClose = vi.fn()
    render(<GoToDialog clock={clock} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Trigger an uncertainty shock…' }))
    expect(screen.getByText(/volatility rises to 1.4 to 1.9 times/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Trigger it' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(fetchMock.mock.calls.find(([url, init]) => url === '/api/shock' && init?.method === 'POST')).toBeTruthy()
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

describe('GoToDialog: real data', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  function mockReal(status: object) {
    const fn = vi.fn(async () => ({ ok: true, json: async () => status }))
    vi.stubGlobal('fetch', fn)
    return fn
  }

  it('has no sync option any more', () => {
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    expect(screen.queryByRole('button', { name: /Sync/ })).not.toBeInTheDocument()
  })

  it('says how far the real prices run, with the year, and updates on request', async () => {
    const status = { configured: true, available: 40, total: 40, newest: '2026-10-30', history: { configured: true, available: 40, total: 40, newest: '2026-10-30' }, macro: { configured: true, available: 10, total: 10 } }
    const fn = vi.fn(async (url: string, init?: RequestInit) => ({
      ok: true,
      json: async () => (url === '/api/real-history/update' && init?.method === 'POST' ? { prices: { SPY: 2, QQQ: 2 }, macro: { DFF: 2 }, problems: [], status } : status),
    }))
    vi.stubGlobal('fetch', fn)
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText('Real prices through Oct 30, 2026 (40 of 40 symbols).')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Update real history' }))
    expect(await screen.findByText('Added 4 days of prices (2 symbols) and 2 economic readings.')).toBeInTheDocument()
  })

  it('says how far option prices are fitted to real options', async () => {
    const options = { configured: true, earnings: false, calibrated: 38, total: 40, oldest: '2026-10-29', newest: '2026-10-30', last_error: "XLF's options: Alpaca says too many requests" }
    mockReal({ configured: true, available: 40, total: 40, newest: '2026-10-30', history: { configured: true, available: 40, total: 40, newest: '2026-10-30' }, options })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText(/fitted to real options for 38 of 40 symbols \(Oct 29–Oct 30\), again daily/)).toBeInTheDocument()
    expect(screen.getByText(/Real earnings dates need an Alpha Vantage key/)).toBeInTheDocument()
    expect(screen.getByText("Option data: XLF's options: Alpaca says too many requests")).toBeInTheDocument()
  })

  it('shows option fitting in progress', async () => {
    const options = { configured: true, earnings: true, calibrated: 3, total: 40, running: { done: 12, total: 40 } }
    mockReal({ configured: true, available: 0, total: 40, options })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText(/fitting to real options, 12 of 40 symbols done/)).toBeInTheDocument()
    expect(screen.queryByText(/Alpha Vantage/)).not.toBeInTheDocument()
  })

  it('shows the last quote fetch problem', async () => {
    mockReal({ configured: true, available: 0, total: 40, last_error: 'Alpaca rejected the API keys (401).' })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText(/The last real quote fetch failed: Alpaca rejected the API keys/)).toBeInTheDocument()
  })

  it('ignores a status that arrives after Back', async () => {
    let answer: (v: unknown) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((resolve) => (answer = resolve))),
    )
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    answer({ ok: true, json: async () => ({ configured: true, available: 5, total: 40 }) })
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.getByLabelText('Market date and time')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Real data' })).not.toBeInTheDocument()
  })

  it('explains when there are no keys', async () => {
    mockReal({ configured: false, available: 0, total: 40 })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText(/Real prices need TIINGO_API_KEY/)).toBeInTheDocument()
    expect(screen.getByText(/Economic series need FRED_API_KEY/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update real history' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByLabelText('Market date and time')).toBeInTheDocument()
  })

  it('names the missing key when only one source is set up', async () => {
    mockReal({ configured: true, available: 0, total: 40, macro: { configured: true, available: 10, total: 10 } })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText(/Real prices need TIINGO_API_KEY/)).toBeInTheDocument()
    expect(screen.getByText('Economic series: 10 of 10.')).toBeInTheDocument()
  })

  it('says when real prices are configured but not downloaded', async () => {
    mockReal({ configured: true, available: 0, total: 40, history: { configured: true, available: 0, total: 40 }, macro: { configured: true, available: 0, total: 10 } })
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText('Real prices: none downloaded yet.')).toBeInTheDocument()
  })

  it("explains that newer days only reach markets started later, in the real past too", async () => {
    mockReal({ configured: true, available: 40, total: 40, history: { configured: true, available: 40, total: 40, newest: '2026-11-02' }, macro: { configured: true, available: 10, total: 10 } })
    render(<GoToDialog clock={{ ...clock, time: '2008-10-10T19:30:00Z', real_through: '2026-10-30' }} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Real data…' }))
    expect(await screen.findByText('Real prices through Nov 2, 2026 (40 of 40 symbols).')).toBeInTheDocument()
    expect(screen.getByText(/replays real days through Oct 30, 2026\. Newer days apply to markets started later/)).toBeInTheDocument()
  })

  it('"Last real day" jumps the input to the close of the market\'s newest real day', () => {
    render(<GoToDialog clock={{ ...clock, real_through: '2026-10-30' }} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Last real day' }))
    expect(screen.getByLabelText('Market date and time')).toHaveValue('2026-10-30T15:59')
  })

  it('has no "Last real day" without real data', () => {
    render(<GoToDialog clock={clock} onClose={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Last real day' })).not.toBeInTheDocument()
  })
})


describe('GoToDialog: the real past', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })
  const past = { ...clock, time: '2008-10-10T19:30:00Z', real_through: '2026-10-30' }

  it("explains up front that the real past can't be shocked, and Back clears an old error", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 422, json: async () => ({ detail: "the real past can't be shocked" }) })))
    const { rerender } = render(<GoToDialog clock={clock} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Trigger an uncertainty shock…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Trigger it' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("can't be shocked")
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    rerender(<GoToDialog clock={past} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Trigger an uncertainty shock…' }))
    expect(screen.getByText(/real past/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Trigger it' })).toBeDisabled()
  })
})
