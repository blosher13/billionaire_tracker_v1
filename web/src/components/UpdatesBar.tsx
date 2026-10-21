import { useEffect, useState } from 'react'
import { getStatus, type UpdateStatus } from '../api'

const REFRESH_MS = 60_000

/** When the data was last refreshed and when it's next due: jet positions and weather, and X posts. */
export function UpdatesBar() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)

  useEffect(() => {
    const load = () =>
      getStatus()
        .then(setStatus)
        .catch(() => undefined)
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [])

  if (!status) return null

  return (
    <div className="updates" role="status" aria-label="Data updates">
      <p>
        <strong>Jet positions and weather</strong>
        <span>
          {status.positions_updated_at ? `Updated ${clock(status.positions_updated_at)}` : 'Not updated yet'}
          {status.next_positions_at && `, next ${clock(status.next_positions_at)}`}
        </span>
      </p>
      <p>
        <strong>X posts</strong>
        <span>
          {status.x_updated_at ? `Updated ${clock(status.x_updated_at)}` : 'Not updated yet'}
          {status.x_paused ? ', new posts paused' : status.next_x_at && `, next ${clock(status.next_x_at)}`}
        </span>
      </p>
    </div>
  )
}

/** "9:26 AM", "midnight", "yesterday 6:00 PM", "tomorrow 9:00 AM" or "Oct 18, 6:00 PM" (viewer's time). */
function clock(iso: string): string {
  const d = new Date(iso)
  if (d.getHours() === 0 && d.getMinutes() === 0) {
    if (sameDay(d, 1)) return 'at midnight'
  }
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  if (sameDay(d, 0)) return time
  if (sameDay(d, -1)) return `yesterday ${time}`
  if (sameDay(d, 1)) return `tomorrow ${time}`
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function sameDay(d: Date, offsetDays: number): boolean {
  const ref = new Date()
  ref.setDate(ref.getDate() + offsetDays)
  return d.toDateString() === ref.toDateString()
}
