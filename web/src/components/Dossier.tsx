import { compass, num, timeAgo } from '../format'
import type { Aircraft, Flight } from '../types'

// Positions are fetched every hour, so only warn when one is older than an update cycle plus margin.
const STALE_AFTER_MS = 2 * 60 * 60 * 1000

interface Props {
  aircraft: Aircraft | undefined
  flight: Flight | null
}

/** Everything about the selected billionaire's jet: who, where it is, and the weather there. */
export function Dossier({ aircraft, flight }: Props) {
  const stale = flight != null && Date.now() - Date.parse(flight.ts) > STALE_AFTER_MS
  const plane = [aircraft?.registration, aircraft?.aircraft_type].filter(Boolean).join(', ')

  return (
    <section className="dossier" aria-label={`${aircraft?.owner ?? 'Aircraft'} details`}>
      <header className="dossier-head">
        <h2>{aircraft?.owner ?? 'Unknown owner'}</h2>
        <p className="dossier-plane">{plane || aircraft?.hex}</p>
        <PhotoCredit aircraft={aircraft} />
      </header>

      {!flight ? (
        <p className="quiet">No position yet. Positions are updated every hour.</p>
      ) : (
        <>
          <dl className="readouts">
            <Readout label="Altitude" value={flight.on_ground ? 'On ground' : flight.altitude_ft == null ? '—' : `${num(flight.altitude_ft)} ft`} />
            <Readout label="Speed" value={flight.ground_speed_kt == null ? '—' : `${num(flight.ground_speed_kt)} kt`} />
            <Readout
              label="Heading"
              value={flight.track_deg == null ? '—' : `${num(flight.track_deg)}° ${compass(flight.track_deg)}`}
            />
            <Readout label="Callsign" value={flight.flight ?? '—'} />
            <Readout label="Position" value={`${flight.lat.toFixed(3)}, ${flight.lon.toFixed(3)}`} />
            <Readout label="Last seen" value={timeAgo(flight.ts)} />
          </dl>
          {stale && (
            <p className="notice">
              No newer position was reported at the last update, so this is where the jet was last seen. It is
              probably parked or out of receiver range.
            </p>
          )}
          <Weather flight={flight} />
        </>
      )}
    </section>
  )
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="readout">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function Weather({ flight }: { flight: Flight }) {
  if (flight.temp_c == null) return <p className="quiet">No weather observation yet.</p>
  return (
    <div className="weather">
      <p className="weather-temp">{num(flight.temp_c)}°</p>
      <div>
        <p className="weather-desc">{flight.weather_description}</p>
        <p className="quiet">
          Feels like {num(flight.feels_like_c)}°, humidity {num(flight.humidity_pct)}%
          {flight.wind_speed_ms != null && `, wind ${num(flight.wind_speed_ms * 3.6)} km/h ${compass(flight.wind_deg)}`}
        </p>
        <p className="quiet">Weather where the jet is, observed {timeAgo(flight.weather_ts)}</p>
      </div>
    </div>
  )
}

/** Attribution for the owner's photo (required by Creative Commons licenses, e.g. Wikimedia). */
function PhotoCredit({ aircraft }: { aircraft: Aircraft | undefined }) {
  if (!aircraft?.icon_url || !aircraft.icon_credit) return null
  return (
    <p className="credit">
      {aircraft.icon_source_url ? (
        <a href={aircraft.icon_source_url} target="_blank" rel="noreferrer">
          {aircraft.icon_credit}
        </a>
      ) : (
        aircraft.icon_credit
      )}
    </p>
  )
}
