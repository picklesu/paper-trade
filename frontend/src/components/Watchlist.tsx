import { useEffect, useRef, useState } from 'react'
import { direction, percent, price } from '../lib/format'
import type { Quote } from '../lib/types'

interface Props {
  quotes: Quote[]
  selected: string
  onSelect: (symbol: string) => void
}

export function Watchlist({ quotes, selected, onSelect }: Props) {
  return (
    <table className="grid-table watchlist-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th className="num">Last</th>
          <th className="num">Chg %</th>
        </tr>
      </thead>
      <tbody>
        {quotes.map((q) => (
          <Row key={q.symbol} quote={q} selected={q.symbol === selected} onSelect={onSelect} />
        ))}
      </tbody>
    </table>
  )
}

const FLASH_MS = 400

function Row({ quote, selected, onSelect }: { quote: Quote; selected: boolean; onSelect: (s: string) => void }) {
  const flash = useTickFlash(quote.last)
  return (
    <tr
      className={selected ? 'selected' : undefined}
      onClick={() => onSelect(quote.symbol)}
      aria-selected={selected}
      title={quote.name}
    >
      <td className="sym">{quote.symbol}</td>
      <td className={`num flash-${flash}`}>{price(quote.last)}</td>
      <td className={`num ${direction(quote.change)}`}>{percent(quote.change_pct)}</td>
    </tr>
  )
}

/** Briefly returns 'up' or 'down' after the value changes, like a broker's tick flash. */
function useTickFlash(value: number): 'up' | 'down' | 'none' {
  const prev = useRef(value)
  const [flash, setFlash] = useState<'up' | 'down' | 'none'>('none')

  useEffect(() => {
    if (value === prev.current) return
    setFlash(value > prev.current ? 'up' : 'down')
    prev.current = value
    const t = setTimeout(() => setFlash('none'), FLASH_MS)
    return () => clearTimeout(t)
  }, [value])

  return flash
}
