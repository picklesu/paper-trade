import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createWatchlist, deleteWatchlist, updateWatchlist } from '../lib/api'
import { direction, percent, price, signed, volume } from '../lib/format'
import { useStoredState } from '../lib/storage'
import type { QuoteMap } from '../lib/stream'
import type { ClockState, Quote, QuoteStats, SymbolInfo, Watchlist as List } from '../lib/types'
import {
  COLUMN_BY_ID,
  COLUMNS,
  DEFAULT_COLUMNS,
  high52,
  low52,
  moveSymbol,
  nextSort,
  sanitizeColumns,
  sanitizeSort,
  sortRows,
  toggleColumn,
  type ColumnId,
  type Row as RowData,
  type Sort,
  type SortKey,
} from '../lib/watchlist'
import { useSparklines } from '../lib/sparklines'
import { RangeBar, Sparkline } from './Sparkline'
import { SymbolSearch } from './SymbolSearch'

interface Props {
  lists: List[]
  list: List
  onPickList: (id: number) => void
  quotes: QuoteMap
  stats: Record<string, QuoteStats>
  held: ReadonlyMap<string, number>
  symbols: SymbolInfo[]
  clock: ClockState | null
  selected: string
  onSelect: (symbol: string) => void
}

/** Named lists of symbols: pick a list, add, remove and drag symbols into order,
 *  sort by any column, and choose the columns. */
export function Watchlist({ lists, list, onPickList, quotes, stats, held, symbols, clock, selected, onSelect }: Props) {
  const [columns, setColumns] = useStoredState<ColumnId[]>('paper-trade.watchlist.columns', DEFAULT_COLUMNS, sanitizeColumns)
  const [sort, setSort] = useStoredState<Sort | null>('paper-trade.watchlist.sort', null, sanitizeSort)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  // A change just made, shown until the server's copy of the list arrives.
  const [pending, setPending] = useState<{ base: List; symbols: string[] } | null>(null)
  const order = pending?.base === list ? pending.symbols : list.symbols

  const run = useCallback(async (action: () => Promise<unknown>) => {
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPending(null)
    }
  }, [])

  const setSymbols = useCallback(
    (next: string[]) => {
      setPending({ base: list, symbols: next })
      void run(() => updateWatchlist(list.id, { symbols: next }))
    },
    [list, run],
  )
  const remove = useCallback((symbol: string) => setSymbols(order.filter((s) => s !== symbol)), [order, setSymbols])

  const spark = useSparklines(order, clock, columns.includes('spark'))
  const rows = sortRows(
    order.map((symbol) => ({ symbol, quote: quotes.get(symbol), stats: stats[symbol], qty: held.get(symbol) ?? 0 })),
    sort,
  )
  const present = useMemo(() => new Set(order), [order])

  // Dragging a row to reorder (only in the list's own order).
  const [dragging, setDragging] = useState<string | null>(null)
  const [dropBefore, setDropBefore] = useState<string | null | undefined>(undefined)
  const drag: Drag = useMemo(
    () =>
      sort
        ? null
        : {
            start: setDragging,
            over: setDropBefore,
            drop: (before: string | null) => {
              setDragging(null)
              setDropBefore(undefined)
              if (dragging) {
                const next = moveSymbol(order, dragging, before)
                if (next !== order) setSymbols(next)
              }
            },
            end: () => {
              setDragging(null)
              setDropBefore(undefined)
            },
          },
    [sort, dragging, order, setSymbols],
  )

  return (
    <div className="watchlist">
      <ListBar
        lists={lists}
        list={list}
        onPickList={onPickList}
        columns={columns}
        onColumns={setColumns}
        adding={adding}
        onAdding={setAdding}
        run={run}
      />
      {adding && (
        <div className="watchlist-add">
          <SymbolSearch
            symbols={symbols}
            label={`Add a symbol to ${list.name}`}
            placeholder="Add symbol"
            present={present}
            onPick={(symbol) => {
              if (!order.includes(symbol)) setSymbols([...order, symbol])
              onSelect(symbol)
            }}
            onCancel={() => setAdding(false)}
          />
        </div>
      )}
      {error && (
        <p className="watchlist-error" role="alert">
          {error}
        </p>
      )}
      {order.length === 0 ? (
        <p className="watchlist-empty muted">No symbols in {list.name} yet. Add some with +.</p>
      ) : (
        <div className="watchlist-scroll">
          <table className="grid-table watchlist-table">
            <thead>
              <tr>
                <Header label="Symbol" title="Symbol" sortKey="symbol" sort={sort} onSort={setSort} />
                {columns.map((id) => (
                  <Header key={id} label={COLUMN_BY_ID[id].label} title={COLUMN_BY_ID[id].title} sortKey={id} sort={sort} onSort={setSort} num />
                ))}
                <th className="row-actions" aria-label="Remove" />
              </tr>
            </thead>
            <tbody onDragLeave={(e) => e.currentTarget === e.target && drag?.over(null)}>
              {rows.map((r) => (
                <Row
                  key={r.symbol}
                  symbol={r.symbol}
                  quote={r.quote}
                  stats={r.stats}
                  qty={r.qty}
                  columns={columns}
                  selected={r.symbol === selected}
                  onSelect={onSelect}
                  onRemove={remove}
                  points={spark.series[r.symbol]}
                  step={spark.step}
                  drag={drag}
                  dropHere={dropBefore === r.symbol}
                />
              ))}
              {drag && (
                <tr
                  className={dropBefore === null ? 'drop-end drop-here' : 'drop-end'}
                  onDragOver={(e) => {
                    e.preventDefault()
                    drag.over(null)
                  }}
                  onDrop={(e) => {
                    e.preventDefault()
                    drag.drop(null)
                  }}
                >
                  <td colSpan={columns.length + 2} />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Header({ label, title, sortKey, sort, onSort, num }: { label: string; title: string; sortKey: SortKey; sort: Sort | null; onSort: (s: Sort | null) => void; num?: boolean }) {
  const dir = sort?.key === sortKey ? sort.dir : 0
  return (
    <th className={num ? 'num' : undefined} aria-sort={dir === 1 ? 'ascending' : dir === -1 ? 'descending' : 'none'}>
      <button className="sort-button" title={`Sort by ${title.toLowerCase()}`} onClick={() => onSort(nextSort(sort, sortKey))}>
        {label}
        {dir !== 0 && <span className="sort-arrow">{dir === 1 ? '▲' : '▼'}</span>}
      </button>
    </th>
  )
}

type Drag = { start: (s: string) => void; over: (s: string | null) => void; drop: (before: string | null) => void; end: () => void } | null

// Separate props (not a row object), so memo can tell when nothing changed.
interface RowProps {
  symbol: string
  quote: Quote | undefined
  stats: QuoteStats | undefined
  qty: number
  columns: ColumnId[]
  selected: boolean
  onSelect: (symbol: string) => void
  onRemove: (symbol: string) => void
  points: number[] | undefined
  step: number
  drag: Drag
  dropHere: boolean
}

const FLASH_MS = 400

/** One symbol. Memoized: a tick only re-renders rows whose quote changed. */
const Row = memo(function Row({ symbol, quote: q, stats, qty, columns, selected, onSelect, onRemove, points, step, drag, dropHere }: RowProps) {
  const row: RowData = { symbol, quote: q, stats, qty }
  const flash = useTickFlash(q?.last)
  const classes = [selected && 'selected', dropHere && 'drop-here'].filter(Boolean).join(' ')
  return (
    <tr
      className={classes || undefined}
      onClick={() => onSelect(symbol)}
      aria-selected={selected}
      title={q?.name}
      draggable={drag !== null}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move'
        e.dataTransfer.setData('text/plain', symbol)
        drag?.start(symbol)
      }}
      onDragOver={(e) => {
        if (!drag) return
        e.preventDefault()
        drag.over(symbol)
      }}
      onDrop={(e) => {
        e.preventDefault()
        drag?.drop(symbol)
      }}
      onDragEnd={() => drag?.end()}
    >
      <td className="sym">
        {symbol}
        {qty > 0 && <span className="held-dot" title={`You hold ${qty.toLocaleString()} shares`} aria-label={`held: ${qty} shares`} />}
      </td>
      {columns.map((id) => (
        <Cell key={id} id={id} row={row} flash={flash} points={points} step={step} />
      ))}
      <td className="row-actions">
        <button
          className="remove-button"
          aria-label={`Remove ${symbol}`}
          title={`Remove ${symbol} from this list`}
          onClick={(e) => {
            e.stopPropagation()
            onRemove(symbol)
          }}
        >
          ×
        </button>
      </td>
    </tr>
  )
})

const dash = <span className="muted">—</span>

function Cell({ id, row, flash, points, step }: { id: ColumnId; row: RowData; flash: string; points: number[] | undefined; step: number }) {
  const q: Quote | undefined = row.quote
  if (!q && id !== 'position' && id !== 'avg_volume') return <td className="num">{dash}</td>
  switch (id) {
    case 'last':
      return <td className={`num flash-${flash}`}>{price(q!.last)}</td>
    case 'change':
      return <td className={`num ${direction(q!.change)}`}>{signed(q!.change)}</td>
    case 'change_pct':
      return <td className={`num ${direction(q!.change)}`}>{percent(q!.change_pct)}</td>
    case 'spark':
      return (
        <td className="num spark-cell">
          <Sparkline points={points} last={q!.last} prevClose={q!.prev_close} step={step} />
        </td>
      )
    case 'range':
      return (
        <td className="num range-cell">
          <RangeBar low={q!.low} high={q!.high} value={q!.last} label={`Day range ${price(q!.low)}–${price(q!.high)}`} />
        </td>
      )
    case 'bid':
      return <td className="num">{price(q!.bid)}</td>
    case 'ask':
      return <td className="num">{price(q!.ask)}</td>
    case 'bid_size':
      return <td className="num">{q!.bid_size.toLocaleString()}</td>
    case 'ask_size':
      return <td className="num">{q!.ask_size.toLocaleString()}</td>
    case 'spread':
      return <td className="num">{price(q!.ask - q!.bid)}</td>
    case 'volume':
      return <td className="num">{volume(q!.volume)}</td>
    case 'avg_volume':
      return <td className="num">{row.stats ? volume(row.stats.avg_volume) : dash}</td>
    case 'high_52w': {
      const v = high52(row)
      return <td className="num">{v === null ? dash : price(v)}</td>
    }
    case 'low_52w': {
      const v = low52(row)
      return <td className="num">{v === null ? dash : price(v)}</td>
    }
    case 'position':
      return <td className="num">{row.qty ? row.qty.toLocaleString() : dash}</td>
  }
}

/** Briefly returns 'up' or 'down' after the value changes, like a broker's tick flash. */
function useTickFlash(value: number | undefined): 'up' | 'down' | 'none' {
  const prev = useRef(value)
  const [flash, setFlash] = useState<'up' | 'down' | 'none'>('none')

  useEffect(() => {
    if (value === prev.current) return
    const before = prev.current
    prev.current = value
    if (value === undefined || before === undefined) return
    setFlash(value > before ? 'up' : 'down')
    const t = setTimeout(() => setFlash('none'), FLASH_MS)
    return () => clearTimeout(t)
  }, [value])

  return flash
}

interface BarProps {
  lists: List[]
  list: List
  onPickList: (id: number) => void
  columns: ColumnId[]
  onColumns: (c: ColumnId[]) => void
  adding: boolean
  onAdding: (on: boolean) => void
  run: (action: () => Promise<unknown>) => Promise<void>
}

/** The list picker, the add button and the list menu (new, rename, delete, columns). */
function ListBar({ lists, list, onPickList, columns, onColumns, adding, onAdding, run }: BarProps) {
  const [menu, setMenu] = useState(false)
  const [naming, setNaming] = useState<'new' | 'rename' | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menu) return
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setMenu(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMenu(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [menu])

  if (naming) {
    return (
      <div className="watchlist-bar">
        <NameField
          initial={naming === 'rename' ? list.name : ''}
          label={naming === 'rename' ? `Rename ${list.name}` : 'New watchlist name'}
          onDone={(name) => {
            setNaming(null)
            if (name === null) return
            if (naming === 'rename') void run(() => updateWatchlist(list.id, { name }))
            else void run(async () => onPickList((await createWatchlist(name)).id))
          }}
        />
      </div>
    )
  }

  return (
    <div className="watchlist-bar" ref={root}>
      <select className="input list-select" aria-label="Watchlist" value={list.id} onChange={(e) => onPickList(Number(e.target.value))}>
        {lists.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name} ({w.symbols.length})
          </option>
        ))}
      </select>
      <button className={adding ? 'ctl icon-ctl active' : 'ctl icon-ctl'} aria-label="Add symbol" title="Add a symbol" aria-pressed={adding} onClick={() => onAdding(!adding)}>
        +
      </button>
      <div className="list-menu">
        <button
          className="ctl icon-ctl"
          aria-label="Watchlist menu"
          aria-haspopup="true"
          aria-expanded={menu}
          onClick={() => {
            setMenu(!menu)
            setConfirmDelete(false)
          }}
        >
          ⋯
        </button>
        {menu && (
          <div className="menu-panel list-menu-panel" role="menu" aria-label="Watchlist menu">
            <button role="menuitem" className="menu-item" onClick={() => (setMenu(false), setNaming('new'))}>
              New list…
            </button>
            <button role="menuitem" className="menu-item" onClick={() => (setMenu(false), setNaming('rename'))}>
              Rename “{list.name}”…
            </button>
            {lists.length > 1 &&
              (confirmDelete ? (
                <div className="menu-confirm">
                  <span>Delete “{list.name}”?</span>
                  <button
                    className="btn danger"
                    onClick={() => {
                      setMenu(false)
                      void run(() => deleteWatchlist(list.id))
                    }}
                  >
                    Delete
                  </button>
                  <button className="btn" onClick={() => setConfirmDelete(false)}>
                    Keep
                  </button>
                </div>
              ) : (
                <button role="menuitem" className="menu-item" onClick={() => setConfirmDelete(true)}>
                  Delete “{list.name}”…
                </button>
              ))}
            <div className="menu-sep" />
            <div className="menu-heading">Columns</div>
            {COLUMNS.map((c) => (
              <button
                key={c.id}
                role="menuitemcheckbox"
                aria-checked={columns.includes(c.id)}
                className="menu-item"
                onClick={() => onColumns(toggleColumn(columns, c.id))}
              >
                <span className="check">{columns.includes(c.id) ? '✓' : ''}</span>
                {c.title}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function NameField({ initial, label, onDone }: { initial: string; label: string; onDone: (name: string | null) => void }) {
  const [name, setName] = useState(initial)
  const done = useRef(false)
  const finish = (value: string | null) => {
    if (done.current) return
    done.current = true
    onDone(value)
  }
  return (
    <input
      className="input list-name"
      aria-label={label}
      placeholder={label}
      value={name}
      maxLength={40}
      autoFocus
      onChange={(e) => setName(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') finish(name.trim() || null)
        else if (e.key === 'Escape') finish(null)
      }}
      onBlur={() => finish(null)}
    />
  )
}
