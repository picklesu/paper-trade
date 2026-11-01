import type { ConnectionStatus } from '../lib/stream'
import { direction, dollars, signedDollars } from '../lib/format'
import type { Account, ClockState, Halt, ProgressMessage, Regime } from '../lib/types'
import { PanelsMenu } from '../layout/PanelsMenu'
import { ClockControls } from './ClockControls'
import { FullscreenButton } from './FullscreenButton'

interface Props {
  status: ConnectionStatus
  clock: ClockState | null
  regime: Regime | null
  shock?: number
  shockPending?: boolean
  halt?: Halt | null
  progress: ProgressMessage | null
  account: Account | null
}

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: 'Connecting…',
  connected: 'Connected',
  disconnected: 'Disconnected',
}

export function TopBar({ status, clock, regime, shock, shockPending, halt, progress, account }: Props) {
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
      <ClockControls clock={clock} regime={regime} shock={shock} shockPending={shockPending} halt={halt} progress={progress} />
      <PanelsMenu />
      <FullscreenButton />
      <div className={`conn conn-${status}`} role="status" title={STATUS_LABEL[status]}>
        <span className="dot" />
        <span className="conn-text">{STATUS_LABEL[status]}</span>
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
