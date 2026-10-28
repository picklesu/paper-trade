import type { ConnectionStatus } from '../lib/stream'
import { direction, dollars, signedDollars } from '../lib/format'
import type { Account, ClockState, ProgressMessage, Regime } from '../lib/types'
import { PanelsMenu } from '../layout/PanelsMenu'
import { ClockControls } from './ClockControls'
import { FullscreenButton } from './FullscreenButton'

interface Props {
  status: ConnectionStatus
  clock: ClockState | null
  regime: Regime | null
  progress: ProgressMessage | null
  account: Account | null
}

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: 'Connecting…',
  connected: 'Connected',
  disconnected: 'Disconnected',
}

export function TopBar({ status, clock, regime, progress, account }: Props) {
  return (
    <header className="topbar">
      <div className="brand">Paper Trade</div>
      <div className="account">
        <Stat label="Net Liq" value={account ? dollars(account.net_liq) : '—'} />
        <Stat label="Buying Power" value={account ? dollars(account.buying_power) : '—'} />
        <Stat
          label="Day P&L"
          value={account ? signedDollars(account.day_pnl) : '—'}
          className={account ? direction(account.day_pnl) : undefined}
        />
      </div>
      <ClockControls clock={clock} regime={regime} progress={progress} />
      <PanelsMenu />
      <FullscreenButton />
      <div className={`conn conn-${status}`} role="status">
        <span className="dot" />
        {STATUS_LABEL[status]}
      </div>
    </header>
  )
}

function Stat({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={className ? `stat-value ${className}` : 'stat-value'}>{value}</span>
    </div>
  )
}
