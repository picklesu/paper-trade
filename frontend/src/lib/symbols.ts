import type { SymbolInfo } from './types'

/** An index is quoted and charted but never traded and has no options (the VIX). */
export function isIndex(symbol: string): boolean {
  return symbol === 'VIX'
}

/** Symbols matching what was typed, best first: the exact symbol, symbols starting
 *  with it, names with a word starting with it, then names containing it. */
export function matchSymbols(symbols: SymbolInfo[], text: string, limit = 8): SymbolInfo[] {
  const q = text.trim().toUpperCase()
  if (!q) return []
  const rank = (s: SymbolInfo): number => {
    const name = s.name.toUpperCase()
    if (s.symbol === q) return 0
    if (s.symbol.startsWith(q)) return 1
    if (name.split(/[^A-Z0-9&]+/).some((w) => w.startsWith(q))) return 2
    if (name.includes(q)) return 3
    return -1
  }
  return symbols
    .map((s) => [rank(s), s] as const)
    .filter(([r]) => r >= 0)
    .sort((a, b) => a[0] - b[0] || a[1].symbol.localeCompare(b[1].symbol))
    .slice(0, limit)
    .map(([, s]) => s)
}
