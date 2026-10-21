import { AdvancedMarker, APIProvider, Map as GoogleMap, useMap, useMapsLibrary } from '@vis.gl/react-google-maps'
import { useEffect, useRef, type CSSProperties } from 'react'
import { initials, timeAgo } from '../format'
import type { Aircraft, Flight, Post } from '../types'
import { PostPins } from './PostPins'

interface Props {
  apiKey: string
  aircraft: Aircraft[]
  flights: Record<string, Flight>
  /** One colour per aircraft (matches its strip and history track). */
  colors: Record<string, string>
  selectedHex: string | null
  onSelectAircraft: (hex: string) => void
  /** Posts of the selected aircraft's owner (pins are only shown for the selected one). */
  posts: Post[]
  selectedPost: Post | null
  onSelectPost: (post: Post | null) => void
}

const USA = { lat: 39.8, lng: -98.6 }
const SELECTED_ZOOM = 7
const MAX_FLEET_ZOOM = 7

/** Every tracked jet on one map. The selected one is enlarged and followed; the rest stay visible. */
export function FlightMap({ apiKey, aircraft, flights, colors, selectedHex, onSelectAircraft, posts, selectedPost, onSelectPost }: Props) {
  return (
    <APIProvider apiKey={apiKey}>
      {/* Advanced markers (custom HTML) need a Map ID; DEMO_MAP_ID is Google's free dev ID. */}
      <GoogleMap mapId="DEMO_MAP_ID" defaultCenter={USA} defaultZoom={4} gestureHandling="greedy" className="map">
        {aircraft.map((a) => {
          const flight = flights[a.hex]
          if (!flight) return null
          const selected = a.hex === selectedHex
          return (
            <AdvancedMarker
              key={a.hex}
              position={{ lat: flight.lat, lng: flight.lon }}
              title={`${a.owner ?? a.hex} — ${a.registration ?? ''} · last seen ${timeAgo(flight.ts)}`}
              zIndex={selected ? 1000 : 10}
              onClick={() => onSelectAircraft(a.hex)}
            >
              <PlaneMarker
                aircraft={a}
                color={colors[a.hex]}
                heading={flight.track_deg}
                selected={selected}
                dimmed={selectedHex != null && !selected}
                label
              />
            </AdvancedMarker>
          )
        })}

        <PostPins posts={posts} selectedPost={selectedPost} onSelectPost={onSelectPost} />

        <MapCamera flights={flights} selectedHex={selectedHex} selectedPost={selectedPost} />
      </GoogleMap>
    </APIProvider>
  )
}

interface MarkerProps {
  aircraft: Aircraft | undefined
  heading: number | null
  /** Ring colour: the person's colour. */
  color?: string
  selected?: boolean
  dimmed?: boolean
  /** Show the owner's name under the photo. */
  label?: boolean
}

/** The owner's photo (from config/billionaires.csv) with a pointer showing the heading. */
export function PlaneMarker({ aircraft, heading, color, selected = false, dimmed = false, label = false }: MarkerProps) {
  return (
    <div
      className={`marker${selected ? ' marker-selected' : ''}${dimmed ? ' marker-dimmed' : ''}`}
      style={color ? ({ '--ring': color } as CSSProperties) : undefined}
    >
      <div className="marker-body">
        {heading != null && (
          <div className="marker-heading" style={{ transform: `rotate(${heading}deg)` }}>
            <span />
          </div>
        )}
        {aircraft?.icon_url ? (
          <img className="marker-photo" src={aircraft.icon_url} alt={aircraft.owner ?? aircraft.hex} />
        ) : (
          <div className="marker-photo marker-initials">{initials(aircraft?.owner ?? null, aircraft?.hex ?? '?')}</div>
        )}
      </div>
      {label && aircraft?.owner && <span className="marker-label">{aircraft.owner}</span>}
    </div>
  )
}

/**
 * Camera rules:
 * - nothing selected: fit all jets in view (on first load and whenever the selection is cleared)
 * - an aircraft selected: zoom to it once, then follow it as it moves
 * - a post selected: pan to the post
 */
function MapCamera({
  flights,
  selectedHex,
  selectedPost,
}: {
  flights: Record<string, Flight>
  selectedHex: string | null
  selectedPost: Post | null
}) {
  const map = useMap()
  const core = useMapsLibrary('core')
  const framed = useRef<string | null>(null) // what we last framed: 'fleet' or an aircraft hex

  const flightCount = Object.keys(flights).length
  useEffect(() => {
    if (!map || !core || selectedHex || flightCount === 0 || framed.current === 'fleet') return
    const bounds = new core.LatLngBounds()
    Object.values(flights).forEach((f) => bounds.extend({ lat: f.lat, lng: f.lon }))
    map.fitBounds(bounds, 80)
    const listener = map.addListener('idle', () => {
      if ((map.getZoom() ?? 0) > MAX_FLEET_ZOOM) map.setZoom(MAX_FLEET_ZOOM)
      listener.remove()
    })
    framed.current = 'fleet'
  }, [map, core, flights, flightCount, selectedHex])

  const selected = selectedHex ? flights[selectedHex] : undefined
  const lat = selected?.lat
  const lon = selected?.lon
  useEffect(() => {
    if (!map || !selectedHex || lat == null || lon == null) return
    if (framed.current !== selectedHex) {
      map.setZoom(SELECTED_ZOOM)
      framed.current = selectedHex
    }
    map.panTo({ lat, lng: lon })
  }, [map, selectedHex, lat, lon])

  useEffect(() => {
    if (map && selectedPost?.plane_lat != null && selectedPost.plane_lon != null) {
      map.panTo({ lat: selectedPost.plane_lat, lng: selectedPost.plane_lon })
    }
  }, [map, selectedPost])

  return null
}
