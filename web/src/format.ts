export function timeAgo(iso: string | null, now = Date.now()): string {
  if (!iso) return '—'
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h ago`
  return `${Math.round(hours / 24)} days ago`
}

/** "2 min before" / "3 h after": how far the aircraft position is from the post time. */
export function offsetLabel(offsetSeconds: number): string {
  const abs = Math.abs(offsetSeconds)
  const span = abs < 90 ? `${abs}s` : abs < 5400 ? `${Math.round(abs / 60)} min` : `${Math.round(abs / 3600)} h`
  return `${span} ${offsetSeconds >= 0 ? 'before' : 'after'}`
}

export function compass(deg: number | null): string {
  if (deg == null) return ''
  const points = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
  return points[Math.round(deg / 45) % 8]
}

export function initials(name: string | null, fallback: string): string {
  if (!name) return fallback.slice(0, 2).toUpperCase()
  return name
    .split(/\s+/)
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
}

export const num = (value: number | null, digits = 0) =>
  value == null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: digits })

/** 1234 → "1.2K", 3400000 → "3.4M". */
export const compact = (value: number | null) =>
  value == null ? '—' : value.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: 1 })

/** "Oct 19, 9:41 PM" (with the year only when it isn't this year). */
export function postTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(d.getFullYear() !== new Date().getFullYear() && { year: 'numeric' }),
    hour: 'numeric',
    minute: '2-digit',
  })
}
