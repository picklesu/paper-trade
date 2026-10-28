import { type ReactNode, useState } from 'react'
import { useStream } from '../lib/stream'
import { AppDataContext } from './appDataContext'

/** Live market and account data plus the selected symbol, shared by every panel. */
export function AppDataProvider({ children }: { children: ReactNode }) {
  const stream = useStream()
  const [selected, setSelected] = useState('SPY')
  return <AppDataContext.Provider value={{ ...stream, selected, setSelected }}>{children}</AppDataContext.Provider>
}
