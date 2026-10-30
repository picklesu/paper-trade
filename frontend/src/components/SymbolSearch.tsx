import { useEffect, useId, useRef, useState } from 'react'
import { matchSymbols } from '../lib/symbols'
import type { SymbolInfo } from '../lib/types'

interface Props {
  symbols: SymbolInfo[]
  onPick: (symbol: string) => void
  /** Escape, or leaving the field empty. */
  onCancel: () => void
  initial?: string
  placeholder: string
  label: string
  /** Symbols to mark as already there (e.g. in the watchlist). */
  present?: ReadonlySet<string>
}

/** A ticker field with suggestions: type a symbol or part of a name, then Enter
 *  (or click) picks the highlighted match. */
export function SymbolSearch({ symbols, onPick, onCancel, initial = '', placeholder, label, present }: Props) {
  const [text, setText] = useState(initial)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()
  const matches = matchSymbols(symbols, text)
  const unknown = text.trim() !== '' && matches.length === 0

  useEffect(() => {
    const el = input.current
    el?.focus()
    // Put the caret after the starting text (the key that opened the lookup).
    el?.setSelectionRange(el.value.length, el.value.length)
  }, [])

  const pick = (symbol: string) => {
    onPick(symbol)
    setText('')
    setActive(0)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (matches.length) setActive((active + (e.key === 'ArrowDown' ? 1 : matches.length - 1)) % matches.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const match = matches[Math.min(active, matches.length - 1)]
      if (match) pick(match.symbol)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onCancel()
    }
  }

  return (
    <div className="symbol-search">
      <input
        ref={input}
        className="input symbol-input"
        value={text}
        placeholder={placeholder}
        aria-label={label}
        role="combobox"
        aria-expanded={matches.length > 0}
        aria-controls={listId}
        aria-activedescendant={matches.length ? `${listId}-${Math.min(active, matches.length - 1)}` : undefined}
        aria-invalid={unknown}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value)
          setActive(0)
        }}
        onKeyDown={onKeyDown}
      />
      {matches.length > 0 && (
        <ul className="symbol-options" role="listbox" id={listId} aria-label="Matching symbols">
          {matches.map((s, i) => (
            <li
              key={s.symbol}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === Math.min(active, matches.length - 1)}
              className={i === Math.min(active, matches.length - 1) ? 'active' : undefined}
              // mousedown, so the field doesn't lose focus (and close) first
              onMouseDown={(e) => {
                e.preventDefault()
                pick(s.symbol)
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="sym">{s.symbol}</span>
              <span className="symbol-name">{s.name}</span>
              {present?.has(s.symbol) && <span className="symbol-present">in list</span>}
            </li>
          ))}
        </ul>
      )}
      {unknown && <div className="symbol-none">No symbol matches “{text.trim()}”</div>}
    </div>
  )
}
