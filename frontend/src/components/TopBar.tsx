import type { ConnectionStatus, Heartbeat } from '../lib/stream'

interface Props {
  status: ConnectionStatus
  heartbeat: Heartbeat | null
}

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: 'Connecting…',
  connected: 'Connected',
  disconnected: 'Disconnected',
}

export function TopBar({ status, heartbeat }: Props) {
  const time = heartbeat ? new Date(heartbeat.server_time).toLocaleTimeString() : '--:--:--'

  return (
    <header className="topbar">
      <div className="brand">Paper Trade</div>
      <div className="account">
        <Stat label="Net Liq" value="—" />
        <Stat label="Buying Power" value="—" />
        <Stat label="Day P&L" value="—" />
      </div>
      <div className="clock" title="Server time">{time}</div>
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
