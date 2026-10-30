import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { AppDataContext, type AppData } from '../layout/appDataContext'
import { SymbolLookup } from './SymbolLookup'

const SYMBOLS = [
  { symbol: 'AAPL', name: 'Apple', sector: null },
  { symbol: 'AMD', name: 'Advanced Micro Devices', sector: null },
  { symbol: 'MSFT', name: 'Microsoft', sector: null },
]

function Harness() {
  const [selected, setSelected] = useState('SPY')
  const [lookup, setLookup] = useState<string | null>(null)
  const data = { symbols: SYMBOLS, selected, setSelected, lookup, setLookup } as unknown as AppData
  return (
    <AppDataContext.Provider value={data}>
      <input aria-label="Quantity" />
      <output aria-label="Selected">{selected}</output>
      <SymbolLookup />
    </AppDataContext.Provider>
  )
}

describe('SymbolLookup', () => {
  it('opens when a letter is typed outside a field, and Enter picks the best match', () => {
    render(<Harness />)
    fireEvent.keyDown(document.body, { key: 'm' })
    const field = screen.getByRole('combobox', { name: 'Look up a symbol' })
    expect(field).toHaveValue('M')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['MSFTMicrosoft', 'AMDAdvanced Micro Devices'])
    fireEvent.change(field, { target: { value: 'ms' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(screen.getByLabelText('Selected')).toHaveTextContent('MSFT')
    expect(screen.queryByRole('search')).not.toBeInTheDocument()
  })

  it('moves through matches with the arrow keys and closes with Escape', () => {
    render(<Harness />)
    fireEvent.keyDown(document.body, { key: 'a' })
    const field = screen.getByRole('combobox')
    fireEvent.keyDown(field, { key: 'ArrowDown' })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(screen.getByLabelText('Selected')).toHaveTextContent('AMD')

    fireEvent.keyDown(document.body, { key: '/' })
    expect(screen.getByRole('combobox')).toHaveValue('')
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    expect(screen.queryByRole('search')).not.toBeInTheDocument()
  })

  it('leaves typing in fields, F (full screen) and shortcuts with modifiers alone', () => {
    render(<Harness />)
    fireEvent.keyDown(screen.getByLabelText('Quantity'), { key: 'a' })
    fireEvent.keyDown(document.body, { key: 'f' })
    fireEvent.keyDown(document.body, { key: 'a', metaKey: true })
    expect(screen.queryByRole('search')).not.toBeInTheDocument()
  })

  it('says when nothing matches', () => {
    render(<Harness />)
    fireEvent.keyDown(document.body, { key: 'z' })
    expect(screen.getByText('No symbol matches “Z”')).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' })
    expect(screen.getByLabelText('Selected')).toHaveTextContent('SPY')
  })
})
