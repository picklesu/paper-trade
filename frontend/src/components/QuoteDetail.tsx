import { direction, percent, price, signed, volume } from '../lib/format'
import type { Quote, QuoteStats } from '../lib/types'
import { high52 as high52Of, low52 as low52Of } from '../lib/watchlist'
import { RangeBar } from './Sparkline'

interface Props {
  quote: Quote
  /** 52-week range and average volume (absent until history is built). */
  stats?: QuoteStats
  /** Clicking the symbol opens the symbol lookup. */
  onLookup?: () => void
}

/** One-line quote header above the chart. */
export function QuoteDetail({ quote, stats, onLookup }: Props) {
  const dir = direction(quote.change)
  const row = { symbol: quote.symbol, quote, stats, qty: 0 }
  const high52 = high52Of(row) ?? undefined
  const low52 = low52Of(row) ?? undefined
  return (
    <div className="quote-detail">
      <button className="quote-symbol" onClick={onLookup} title="Look up another symbol (or just type one)">
        {quote.symbol}
      </button>
      <span className="quote-name">{quote.name}</span>
      <span className="quote-last">{price(quote.last)}</span>
      <span className={`quote-change ${dir}`}>
        {signed(quote.change)} ({percent(quote.change_pct)})
      </span>
      <dl className="quote-stats">
        <Stat label="Bid" value={price(quote.bid)} size={quote.bid_size} />
        <Stat label="Ask" value={price(quote.ask)} size={quote.ask_size} />
        <div className="range-stat">
          <dt>Day</dt>
          <dd>
            {price(quote.low)}
            <RangeBar low={quote.low} high={quote.high} value={quote.last} label={`Day range ${price(quote.low)} to ${price(quote.high)}`} />
            {price(quote.high)}
          </dd>
        </div>
        <Stat label="Volume" value={volume(quote.volume)} />
        <Stat label="Spread" value={price(quote.ask - quote.bid)} minor />
        {high52 !== undefined && low52 !== undefined && (
          <div className="range-stat minor">
            <dt>52W</dt>
            <dd>
              {price(low52)}
              <RangeBar low={low52} high={high52} value={quote.last} label={`52-week range ${price(low52)} to ${price(high52)}`} />
              {price(high52)}
            </dd>
          </div>
        )}
        {stats && <Stat label="Avg Vol" value={volume(stats.avg_volume)} minor />}
        <Stat label="Open" value={price(quote.open)} minor />
        <Stat label="Prev Close" value={price(quote.prev_close)} minor />
      </dl>
    </div>
  )
}

/** `minor` stats are the first to go when the chart is narrow. */
function Stat({ label, value, size, minor }: { label: string; value: string; size?: number; minor?: boolean }) {
  return (
    <div className={minor ? 'minor' : undefined}>
      <dt>{label}</dt>
      <dd>
        {value}
        {size !== undefined && <span className="quote-size" title={`${size.toLocaleString()} shares`}>×{size.toLocaleString()}</span>}
      </dd>
    </div>
  )
}
