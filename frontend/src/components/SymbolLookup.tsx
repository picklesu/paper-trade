import { useEffect, useRef } from 'react'
import { useAppData } from '../layout/appDataContext'
import { dialogOpen, typing } from '../lib/keyboard'
import { SymbolSearch } from './SymbolSearch'

/** Type a ticker anywhere (outside text fields) to look it up: a field opens with
 *  that letter, and Enter shows the symbol in the chart and order entry. "/" opens
 *  it empty. F stays full screen's shortcut. */
export function SymbolLookup() {
  const { symbols, lookup, setLookup, setSelected } = useAppData()
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (lookup !== null) return
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || typing(e.target) || dialogOpen()) return
      if (e.key === '/' || (/^[a-eg-z]$/i.test(e.key))) {
        e.preventDefault()
        setLookup(e.key === '/' ? '' : e.key.toUpperCase())
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [lookup, setLookup])

  useEffect(() => {
    if (lookup === null) return
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setLookup(null)
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [lookup, setLookup])

  if (lookup === null) return null
  return (
    <div className="symbol-lookup" ref={root} role="search" aria-label="Symbol lookup">
      <SymbolSearch
        symbols={symbols}
        initial={lookup}
        label="Look up a symbol"
        placeholder="Symbol or company"
        onPick={(symbol) => {
          setSelected(symbol)
          setLookup(null)
        }}
        onCancel={() => setLookup(null)}
      />
    </div>
  )
}
