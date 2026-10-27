import { BottomTabs } from './components/BottomTabs'
import { Panel, Placeholder } from './components/Panel'
import { TopBar } from './components/TopBar'
import { useStream } from './lib/stream'

export default function App() {
  const { status, heartbeat } = useStream()

  return (
    <div className="app">
      <TopBar status={status} heartbeat={heartbeat} />
      <main className="workspace">
        <Panel title="Watchlist" className="watchlist">
          <Placeholder text="Symbols arrive in Phase 1" />
        </Panel>
        <Panel title="Chart" className="chart">
          <Placeholder text="Charts arrive in Phase 4" />
        </Panel>
        <Panel title="Order Entry" className="ticket">
          <Placeholder text="Order ticket arrives in Phase 2" />
        </Panel>
        <BottomTabs />
      </main>
    </div>
  )
}
