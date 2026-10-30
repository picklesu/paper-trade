import { SymbolLookup } from './components/SymbolLookup'
import { TopBar } from './components/TopBar'
import { AppDataProvider } from './layout/AppData'
import { useAppData } from './layout/appDataContext'
import { LayoutProvider, Workspace } from './layout/Workspace'

export default function App() {
  return (
    <AppDataProvider>
      <LayoutProvider>
        <Shell />
      </LayoutProvider>
    </AppDataProvider>
  )
}

function Shell() {
  const { status, tick, progress } = useAppData()
  return (
    <div className="app">
      <TopBar
        status={status}
        clock={tick?.clock ?? null}
        regime={tick?.regime ?? null}
        progress={progress}
        account={tick?.account ?? null}
      />
      <main className="workspace">
        <Workspace />
      </main>
      <SymbolLookup />
    </div>
  )
}
