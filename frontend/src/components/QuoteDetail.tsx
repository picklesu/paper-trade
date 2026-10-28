import { direction, percent, price, signed, volume } from '../lib/format'
import type { Quote } from '../lib/types'

/** One-line quote header above the chart. */
export function QuoteDetail({ quote }: { quote: Quote }) {
  const dir = direction(quote.change)
  return (
    <div className="quote-detail">
      <div className="quote-head">
        <span className="quote-symbol">{quote.symbol}</span>
        <span className="quote-name">{quote.name}</span>
        <span className="quote-last">{price(quote.last)}</span>
        <span className={dir}>
          {signed(quote.change)} ({percent(quote.change_pct)})
        </span>
      </div>
      <dl className="quote-stats">
        <Stat label="Bid" value={price(quote.bid)} />
        <Stat label="Ask" value={price(quote.ask)} />
        <Stat label="Open" value={price(quote.open)} />
        <Stat label="High" value={price(quote.high)} />
        <Stat label="Low" value={price(quote.low)} />
        <Stat label="Prev Close" value={price(quote.prev_close)} />
        <Stat label="Volume" value={volume(quote.volume)} />
      </dl>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
