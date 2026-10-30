import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChainSide, Expiration, OptionChain as Chain } from '../lib/types'
import { OptionChain } from './OptionChain'

const strikes = [95, 100, 105, 110]
function side(base: number): ChainSide {
  const v = (f: (i: number) => number) => strikes.map((_, i) => f(i))
  return {
    bid: v((i) => base + i),
    ask: v((i) => base + i + 0.05),
    bid_size: v((i) => 10 + i),
    ask_size: v((i) => 20 + i),
    mark: v((i) => base + i + 0.025),
    iv: v(() => 0.2512),
    delta: v((i) => 0.8 - i * 0.2),
    gamma: v(() => 0.0123),
    theta: v(() => -0.0456),
    vega: v(() => 0.111),
    rho: v(() => 0.05),
    volume: v(() => 1200),
    oi: v((i) => 3000 + i),
  }
}
const chain: Chain = {
  symbol: 'AAPL',
  expiration: '2026-11-20',
  kind: 'monthly',
  days: 18,
  years: 0.05,
  spot: 102.5,
  rate: 0.04,
  atm_iv: 0.2512,
  expected_move: 5.74,
  earnings: true,
  calibration: {
    as_of: '2026-10-30',
    skew: -0.41,
    premium: 1.07,
    earnings: { day: '2026-11-02', timing: 'post-market', move: 0.045 },
    fit_error: 0.36,
  },
  strikes,
  call: side(10),
  put: side(1),
}
const expirations: Expiration[] = [
  { date: '2026-11-06', kind: 'weekly', days: 4 },
  { date: '2026-11-20', kind: 'monthly', days: 18 },
]

const rowFor = (strike: string) => screen.getByText(strike).closest('tr')!

describe('OptionChain', () => {
  beforeEach(() => localStorage.clear())

  it('shows calls and puts around the strikes, shading in-the-money options, with a price line', () => {
    render(<OptionChain symbol="AAPL" expirations={expirations} expiration="2026-11-20" onExpiration={() => {}} chain={chain} />)
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Calls', '', 'Puts', 'Open Int', 'Delta', 'IV', 'Ask', 'Bid', 'Strike', 'Bid', 'Ask', 'IV', 'Delta', 'Open Int'])
    const cells = within(rowFor('100.00')).getAllByRole('cell')
    expect(cells.map((c) => c.textContent)).toEqual(['3,001', '0.60', '25.1%', '11.05', '11.00', '100.00', '2.00', '2.05', '25.1%', '0.60', '3,001'])
    expect(cells[0]).toHaveClass('itm') // the 100 call is in the money at 102.50
    expect(cells[6]).not.toHaveClass('itm')
    expect(within(rowFor('105.00')).getAllByRole('cell')[10]).toHaveClass('itm') // the 105 put
    const rows = screen.getAllByRole('row').map((r) => r.getAttribute('aria-label') ?? within(r).queryAllByRole('cell')[5]?.textContent)
    expect(rows.slice(2)).toEqual(['95.00', '100.00', 'Price 102.50', '105.00', '110.00'])
    expect(screen.getByText('Expected move ±5.74 (5.6%)')).toBeInTheDocument()
    expect(screen.getByText('Earnings before expiration')).toBeInTheDocument()
  })

  it('picks the expiration, the number of strikes and the columns (saved)', () => {
    const onExpiration = vi.fn()
    render(<OptionChain symbol="AAPL" expirations={expirations} expiration="2026-11-20" onExpiration={onExpiration} chain={chain} />)
    fireEvent.change(screen.getByLabelText('Expiration'), { target: { value: '2026-11-06' } })
    expect(onExpiration).toHaveBeenCalledWith('2026-11-06')
    expect(screen.getByRole('option', { name: "Nov 6 '26 (4d) W" })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Strikes shown'), { target: { value: '8' } })
    expect(localStorage.getItem('paper-trade.options.strikes')).toBe('8')

    fireEvent.click(screen.getByRole('button', { name: 'Chain columns' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Open interest/ }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Theta/ }))
    expect(screen.getAllByRole('columnheader').slice(3).map((h) => h.textContent)).toEqual(['Theta', 'Delta', 'IV', 'Ask', 'Bid', 'Strike', 'Bid', 'Ask', 'IV', 'Delta', 'Theta'])
    expect(screen.getAllByText('-0.046')).toHaveLength(8)
    expect(JSON.parse(localStorage.getItem('paper-trade.options.columns')!)).toEqual(['bid', 'ask', 'iv', 'delta', 'theta'])
  })

  it('says where the smile comes from, with details on hover', () => {
    const { rerender } = render(<OptionChain symbol="AAPL" expirations={expirations} expiration="2026-11-20" onExpiration={() => {}} chain={chain} />)
    const note = screen.getByText('Calibrated Oct 30')
    expect(note.title).toContain("fitted to AAPL's real options on Oct 30, 2026 (within 0.4 volatility points)")
    expect(note.title).toContain('priced 1.07× the simulated volatility')
    expect(note.title).toContain("a 4.5% move on AAPL's report (Nov 2, after the close)")
    const plain = { ...chain, calibration: { as_of: null, skew: null, premium: 1.12, earnings: null, fit_error: null } }
    rerender(<OptionChain symbol="AAPL" expirations={expirations} expiration="2026-11-20" onExpiration={() => {}} chain={plain} />)
    expect(screen.getByText('Estimated').title).toContain('No real option data for AAPL yet')
  })

  it('shows bid and ask sizes when asked', () => {
    render(<OptionChain symbol="AAPL" expirations={expirations} expiration="2026-11-20" onExpiration={() => {}} chain={chain} />)
    fireEvent.click(screen.getByRole('button', { name: 'Chain columns' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Bid size/ }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Ask size/ }))
    expect(screen.getAllByRole('columnheader').slice(3, 9).map((h) => h.textContent)).toEqual(['Open Int', 'Delta', 'IV', 'Ask Sz', 'Bid Sz', 'Ask'])
    expect(within(rowFor('100.00')).getAllByRole('cell').map((c) => c.textContent)).toContain('21')
  })

  it('says when the chain is loading', () => {
    render(<OptionChain symbol="AAPL" expirations={[]} expiration={null} onExpiration={() => {}} chain={null} />)
    expect(screen.getByText('Loading the chain…')).toBeInTheDocument()
  })
})
