import { direction, percent, price, signed } from '../lib/format'
import type { Quote } from '../lib/types'

interface Props {
  quote: Quote
  /** Clicking the symbol opens the symbol lookup. */
  onLookup?: () => void
}

/** One-line quote header above the chart: the symbol, name, price and change.
 *  (Bid/ask, ranges and volumes are Watchlist columns.) */
export function QuoteDetail({ quote, onLookup }: Props) {
  return (
    <div className="quote-detail">
      <button className="quote-symbol" onClick={onLookup} title="Look up another symbol (or just type one)">
        {quote.symbol}
      </button>
      <span className="quote-name">{quote.name}</span>
      <span className="quote-last">{price(quote.last)}</span>
      <span className={`quote-change ${direction(quote.change)}`}>
        {signed(quote.change)} ({percent(quote.change_pct)})
      </span>
    </div>
  )
}
