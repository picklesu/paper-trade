import { useEffect, useRef, useState } from 'react'
import {
  CHAIN_COLUMN_BY_ID,
  CHAIN_COLUMNS,
  type ChainColumnId,
  DEFAULT_CHAIN_COLUMNS,
  expirationLabel,
  priceRow,
  sanitizeChainColumns,
  STRIKE_COUNTS,
  strikeWindow,
  toggleChainColumn,
} from '../lib/options'
import { price } from '../lib/format'
import { useStoredState } from '../lib/storage'
import type { Expiration, OptionChain as Chain } from '../lib/types'

interface Props {
  symbol: string
  expirations: Expiration[]
  expiration: string | null
  onExpiration: (date: string) => void
  /** The chain for `symbol` and `expiration` (null while it loads). */
  chain: Chain | null
}

/** Calls on the left, puts on the right, strikes in the middle; in-the-money options
 *  shaded and a line where the price is. */
export function OptionChain({ symbol, expirations, expiration, onExpiration, chain }: Props) {
  const [columns, setColumns] = useStoredState<ChainColumnId[]>('paper-trade.options.columns', DEFAULT_CHAIN_COLUMNS, sanitizeChainColumns)
  const [count, setCount] = useStoredState<number>('paper-trade.options.strikes', 12, (v) => (STRIKE_COUNTS.includes(v as number) ? (v as number) : null))

  return (
    <div className="option-chain">
      <div className="chain-bar">
        <span className="chain-symbol">{symbol}</span>
        <select className="input" aria-label="Expiration" value={expiration ?? ''} onChange={(e) => onExpiration(e.target.value)} disabled={!expirations.length}>
          {expirations.map((e) => (
            <option key={e.date} value={e.date}>
              {expirationLabel(e)}
            </option>
          ))}
        </select>
        <select className="input" aria-label="Strikes shown" value={count} onChange={(e) => setCount(Number(e.target.value))}>
          {STRIKE_COUNTS.map((n) => (
            <option key={n} value={n}>
              {n ? `${n} strikes` : 'All strikes'}
            </option>
          ))}
        </select>
        {chain && (
          <span className="chain-info">
            <span title="At-the-money implied volatility">IV {(chain.atm_iv * 100).toFixed(1)}%</span>
            <span title="One standard deviation of the price by expiration">
              Expected move ±{price(chain.expected_move)} ({((chain.expected_move / chain.spot) * 100).toFixed(1)}%)
            </span>
            {chain.earnings && (
              <span className="chain-earnings" title="Implied volatility is higher until the report passes">
                Earnings before expiration
              </span>
            )}
          </span>
        )}
        <ColumnsMenu columns={columns} onColumns={setColumns} />
      </div>
      {chain ? <ChainTable chain={chain} columns={columns} count={count} /> : <p className="muted chain-empty">Loading the chain…</p>}
    </div>
  )
}

function ChainTable({ chain, columns, count }: { chain: Chain; columns: ChainColumnId[]; count: number }) {
  const scroller = useRef<HTMLDivElement>(null)
  // Center the price line when a different chain or strike count shows (not on every tick).
  useEffect(() => {
    const box = scroller.current
    const line = box?.querySelector<HTMLElement>('.price-line')
    if (box && line) box.scrollTop = Math.max(0, line.offsetTop - box.clientHeight / 2)
  }, [chain.symbol, chain.expiration, count])
  const [start, end] = strikeWindow(chain.strikes, chain.spot, count)
  const line = priceRow(chain.strikes, chain.spot)
  const outward = [...columns].reverse() // calls read outward from the strike, leftward
  const rows = []
  for (let i = start; i < end; i++) {
    const k = chain.strikes[i]
    if (i === line) rows.push(<PriceLine key="price" spot={chain.spot} span={columns.length * 2 + 1} />)
    rows.push(
      <tr key={k}>
        {outward.map((id) => (
          <td key={`c-${id}`} className={k < chain.spot ? 'num itm' : 'num'}>
            {CHAIN_COLUMN_BY_ID[id].format(chain.call[id][i])}
          </td>
        ))}
        <td className="strike">{price(k)}</td>
        {columns.map((id) => (
          <td key={`p-${id}`} className={k > chain.spot ? 'num itm' : 'num'}>
            {CHAIN_COLUMN_BY_ID[id].format(chain.put[id][i])}
          </td>
        ))}
      </tr>,
    )
  }
  if (line === end && end > start) rows.push(<PriceLine key="price" spot={chain.spot} span={columns.length * 2 + 1} />)
  return (
    <div className="chain-scroll" ref={scroller}>
      <table className="grid-table chain-table">
        <thead>
          <tr className="chain-sides">
            <th colSpan={columns.length}>Calls</th>
            <th />
            <th colSpan={columns.length}>Puts</th>
          </tr>
          <tr>
            {outward.map((id) => (
              <th key={`c-${id}`} className="num" title={CHAIN_COLUMN_BY_ID[id].title}>
                {CHAIN_COLUMN_BY_ID[id].label}
              </th>
            ))}
            <th className="strike">Strike</th>
            {columns.map((id) => (
              <th key={`p-${id}`} className="num" title={CHAIN_COLUMN_BY_ID[id].title}>
                {CHAIN_COLUMN_BY_ID[id].label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  )
}

function PriceLine({ spot, span }: { spot: number; span: number }) {
  return (
    <tr className="price-line" aria-label={`Price ${price(spot)}`}>
      <td colSpan={span}>
        <span>{price(spot)}</span>
      </td>
    </tr>
  )
}

function ColumnsMenu({ columns, onColumns }: { columns: ChainColumnId[]; onColumns: (c: ChainColumnId[]) => void }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="list-menu chain-menu" ref={root}>
      <button className="ctl icon-ctl" aria-label="Chain columns" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)}>
        ⋯
      </button>
      {open && (
        <div className="menu-panel list-menu-panel" role="menu" aria-label="Chain columns">
          <div className="menu-heading">Columns (both sides)</div>
          {CHAIN_COLUMNS.map((c) => (
            <button key={c.id} role="menuitemcheckbox" aria-checked={columns.includes(c.id)} className="menu-item" onClick={() => onColumns(toggleChainColumn(columns, c.id))}>
              <span className="check">{columns.includes(c.id) ? '✓' : ''}</span>
              {c.title}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
