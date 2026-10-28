import { createContext, useContext } from 'react'
import type { useStream } from '../lib/stream'

export type AppData = ReturnType<typeof useStream> & {
  selected: string
  setSelected: (symbol: string) => void
}

export const AppDataContext = createContext<AppData | null>(null)

/** Live market and account data plus the selected symbol (see AppDataProvider). */
export function useAppData(): AppData {
  const data = useContext(AppDataContext)
  if (!data) throw new Error('useAppData outside AppDataProvider')
  return data
}
