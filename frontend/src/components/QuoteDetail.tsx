import { direction, percent, price, signed, volume } from '../lib/format'
import type { Quote } from '../lib/types'

/** One-line quote header above the chart. */
export function QuoteDetail({ quote }: { quote: Quote }) {
  const dir = direction(quote.change)
  return (
    <div className="quote-detail">
      <span className="quote-symbol">{quote.symbol}</span>
      <span className="quote-name">{quote.name}</span>
      <span className="quote-last">{price(quote.last)}</span>
      <span className={`quote-change ${dir}`}>
        {signed(quote.change)} ({percent(quote.change_pct)})
      </span>
      <dl className="quote-stats">
        <Stat label="Bid" value={price(quote.bid)} />
        <Stat label="Ask" value={price(quote.ask)} />
        <Stat label="Open" value={price(quote.open)} minor />
        <Stat label="High" value={price(quote.high)} />
        <Stat label="Low" value={price(quote.low)} />
        <Stat label="Prev Close" value={price(quote.prev_close)} minor />
        <Stat label="Volume" value={volume(quote.volume)} />
      </dl>
    </div>
  )
}

/** `minor` stats are the first to go when the chart is narrow. */
function Stat({ label, value, minor }: { label: string; value: string; minor?: boolean }) {
  return (
    <div className={minor ? 'minor' : undefined}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
