import type { ConnectionStatus } from '../lib/stream'
import type { ClockState, Regime } from '../lib/types'
import { ClockControls } from './ClockControls'

interface Props {
  status: ConnectionStatus
  clock: ClockState | null
  regime: Regime | null
}

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: 'Connecting…',
  connected: 'Connected',
  disconnected: 'Disconnected',
}

export function TopBar({ status, clock, regime }: Props) {
  return (
    <header className="topbar">
      <div className="brand">Paper Trade</div>
      <div className="account">
        <Stat label="Net Liq" value="—" />
        <Stat label="Buying Power" value="—" />
        <Stat label="Day P&L" value="—" />
      </div>
      <ClockControls clock={clock} regime={regime} />
      <div className={`conn conn-${status}`} role="status">
        <span className="dot" />
        {STATUS_LABEL[status]}
      </div>
    </header>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  )
}
