import { type ReactNode, useMemo, useState } from 'react'
import { useStream } from '../lib/stream'
import { AppDataContext } from './appDataContext'

/** Live market and account data plus the selected symbol, shared by every panel. */
export function AppDataProvider({ children }: { children: ReactNode }) {
  const { status, tick, progress, orders, history } = useStream()
  const [selected, setSelected] = useState('SPY')
  const value = useMemo(
    () => ({ status, tick, progress, orders, history, selected, setSelected }),
    [status, tick, progress, orders, history, selected],
  )
  return <AppDataContext.Provider value={value}>{children}</AppDataContext.Provider>
}
