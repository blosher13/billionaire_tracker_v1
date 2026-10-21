import { useState, type CSSProperties, type FormEvent } from 'react'
import { addAircraft, removeAircraft } from '../api'
import { initials, timeAgo } from '../format'
import type { Aircraft, Flight } from '../types'

interface Props {
  aircraft: Aircraft[]
  selected: string | null
  /** Latest flight per aircraft. */
  flights: Record<string, Flight>
  /** One colour per aircraft: the strip's edge, its map ring and its history track. */
  colors: Record<string, string>
  /** Public site: no add/remove controls (the API rejects changes anyway). */
  readOnly: boolean
  onSelect: (hex: string) => void
  onShowAll: () => void
  onChanged: () => void
}

const HEX_RE = /^[0-9a-f]{6}$/

/**
 * Each billionaire as an air-traffic-control flight progress strip in a rack. Selecting one
 * "cocks" it, sliding it out of line the way controllers flag a strip.
 */
export function StripBay({ aircraft, selected, flights, colors, readOnly, onSelect, onShowAll, onChanged }: Props) {
  return (
    <section className="bay" aria-label="Billionaires">
      <div className="bay-head">
        <h2>{aircraft.length} billionaires</h2>
        {selected && (
          <button className="text-button" onClick={onShowAll}>
            Show everyone
          </button>
        )}
      </div>

      <ul className="strips">
        {aircraft.map((a) => (
          <li key={a.hex}>
            <button
              className={`strip${a.hex === selected ? ' strip-cocked' : ''}`}
              style={{ '--strip-color': colors[a.hex] } as CSSProperties}
              onClick={() => onSelect(a.hex)}
              aria-pressed={a.hex === selected}
            >
              {a.icon_url ? (
                <img className="strip-photo" src={a.icon_url} alt="" />
              ) : (
                <span className="strip-photo strip-initials">{initials(a.owner, a.hex)}</span>
              )}
              <span className="strip-who">
                <span className="strip-name">{a.owner ?? 'Unknown owner'}</span>
                <span className="strip-type">{a.aircraft_type ?? 'Aircraft'}</span>
              </span>
              <span className="strip-reg">{a.registration ?? a.hex.toUpperCase()}</span>
              <StripFields flight={flights[a.hex]} />
            </button>
            {!readOnly && <RemoveButton aircraft={a} onChanged={onChanged} />}
          </li>
        ))}
      </ul>

      {aircraft.length === 0 && <p className="quiet">No aircraft are being tracked yet.</p>}
      {!readOnly && <AddForm onChanged={onChanged} onAdded={onSelect} />}
    </section>
  )
}

/** The boxed cells along the bottom of a strip. */
function StripFields({ flight }: { flight: Flight | undefined }) {
  if (!flight) {
    return (
      <span className="strip-fields">
        <span className="cell">
          <span className="cell-label">Position</span>
          <span className="cell-value">Not seen yet</span>
        </span>
      </span>
    )
  }
  const altitude = flight.on_ground
    ? 'On ground'
    : flight.altitude_ft != null && flight.altitude_ft > 0
      ? `${flight.altitude_ft.toLocaleString()} ft`
      : 'Unknown'
  return (
    <span className="strip-fields">
      <span className="cell">
        <span className="cell-label">Altitude</span>
        <span className="cell-value">{altitude}</span>
      </span>
      <span className="cell">
        <span className="cell-label">Last seen</span>
        <span className="cell-value">{timeAgo(flight.ts)}</span>
      </span>
    </span>
  )
}

function RemoveButton({ aircraft, onChanged }: { aircraft: Aircraft; onChanged: () => void }) {
  async function remove() {
    if (!window.confirm(`Stop tracking ${aircraft.owner ?? aircraft.hex}?`)) return
    await removeAircraft(aircraft.hex)
    onChanged()
  }
  return (
    <button className="strip-remove" onClick={remove} aria-label={`Stop tracking ${aircraft.owner ?? aircraft.hex}`}>
      Remove
    </button>
  )
}

function AddForm({ onChanged, onAdded }: { onChanged: () => void; onAdded: (hex: string) => void }) {
  const [hex, setHex] = useState('')
  const [owner, setOwner] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const value = hex.trim().toLowerCase()
    if (!HEX_RE.test(value)) {
      setError('Enter the 6-character ICAO hex code, for example a835af.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await addAircraft(value, owner.trim())
      setHex('')
      setOwner('')
      onChanged()
      onAdded(value)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="add-form" onSubmit={submit}>
      <input value={hex} onChange={(e) => setHex(e.target.value)} placeholder="ICAO hex" maxLength={6} aria-label="ICAO hex" />
      <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Owner" aria-label="Owner" />
      <button type="submit" disabled={busy}>
        {busy ? 'Adding' : 'Track'}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  )
}
