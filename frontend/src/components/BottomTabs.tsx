import { useState } from 'react'
import { Placeholder } from './Panel'

const TABS = [
  { id: 'positions', label: 'Positions', empty: 'No positions yet (Phase 2)' },
  { id: 'orders', label: 'Orders', empty: 'No orders yet (Phase 2–3)' },
  { id: 'activity', label: 'Activity', empty: 'No fills yet (Phase 2)' },
] as const

type TabId = (typeof TABS)[number]['id']

export function BottomTabs() {
  const [active, setActive] = useState<TabId>('positions')
  const current = TABS.find((t) => t.id === active)!

  return (
    <section className="panel bottom">
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={t.id === active}
            className={t.id === active ? 'tab active' : 'tab'}
            onClick={() => setActive(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="panel-body" role="tabpanel">
        <Placeholder text={current.empty} />
      </div>
    </section>
  )
}
