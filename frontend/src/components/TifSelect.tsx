import { TIF_OPTIONS } from '../lib/orders'
import type { TimeInForce } from '../lib/types'

export function TifSelect({ value, onChange }: { value: TimeInForce; onChange: (tif: TimeInForce) => void }) {
  return (
    <label className="field">
      <span>Time in force</span>
      <select value={value} onChange={(e) => onChange(e.target.value as TimeInForce)}>
        {TIF_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}
