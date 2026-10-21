import { AdvancedMarker, APIProvider, Map as GoogleMap, useMap, useMapsLibrary } from '@vis.gl/react-google-maps'
import { useEffect } from 'react'
import type { Aircraft, HistoryDay, Post, TrackPoint } from '../types'
import { PlaneMarker } from './FlightMap'
import { PostPins } from './PostPins'

interface Props {
  apiKey: string
  data: HistoryDay | null
  aircraft: Aircraft[]
  colors: Record<string, string>
  selectedPost: Post | null
  onSelectPost: (post: Post | null) => void
}

const USA = { lat: 39.8, lng: -98.6 }

/** One day on the map: each jet's flight path, its position at the end of the day, and post pins. */
export function HistoryMap({ apiKey, data, aircraft, colors, selectedPost, onSelectPost }: Props) {
  const byHex = new Map(aircraft.map((a) => [a.hex, a]))

  return (
    <APIProvider apiKey={apiKey}>
      <GoogleMap mapId="DEMO_MAP_ID" defaultCenter={USA} defaultZoom={4} gestureHandling="greedy" className="map">
        {data &&
          Object.entries(data.tracks).map(([hex, points]) => (
            <Polyline key={hex} points={points} color={colors[hex] ?? '#1d4ed8'} />
          ))}

        {data?.end_of_day.map((p) => (
          <AdvancedMarker
            key={p.hex}
            position={{ lat: p.lat, lng: p.lon }}
            title={`${byHex.get(p.hex)?.owner ?? p.owner ?? p.hex}: position at end of day`}
          >
            <PlaneMarker aircraft={byHex.get(p.hex)} color={colors[p.hex]} heading={p.track_deg} label />
          </AdvancedMarker>
        ))}

        <PostPins posts={data?.posts ?? []} selectedPost={selectedPost} onSelectPost={onSelectPost} />

        <FitToDay data={data} selectedPost={selectedPost} />
      </GoogleMap>
    </APIProvider>
  )
}

/** A google.maps.Polyline (the React wrapper doesn't ship one). */
function Polyline({ points, color }: { points: TrackPoint[]; color: string }) {
  const map = useMap()
  const maps = useMapsLibrary('maps')
  useEffect(() => {
    if (!map || !maps || points.length < 2) return
    const path = points.map((p) => ({ lat: p.lat, lng: p.lon }))
    const line = new maps.Polyline({ path, map, strokeColor: color, strokeOpacity: 0.9, strokeWeight: 3, geodesic: true })
    return () => line.setMap(null)
  }, [map, maps, points, color])
  return null
}

/** Zoom to show everything for the day; pan to a post when one is selected. */
function FitToDay({ data, selectedPost }: { data: HistoryDay | null; selectedPost: Post | null }) {
  const map = useMap()
  const core = useMapsLibrary('core')

  useEffect(() => {
    if (!map || !core || !data) return
    const bounds = new core.LatLngBounds()
    Object.values(data.tracks).flat().forEach((p) => bounds.extend({ lat: p.lat, lng: p.lon }))
    data.end_of_day.forEach((p) => bounds.extend({ lat: p.lat, lng: p.lon }))
    data.posts.forEach((p) => p.plane_lat != null && p.plane_lon != null && bounds.extend({ lat: p.plane_lat, lng: p.plane_lon }))
    if (bounds.isEmpty()) return
    map.fitBounds(bounds, 60)
    // A single point would zoom all the way in; cap it.
    const listener = map.addListener('idle', () => {
      if ((map.getZoom() ?? 0) > 9) map.setZoom(9)
      listener.remove()
    })
  }, [map, core, data])

  useEffect(() => {
    if (map && selectedPost?.plane_lat != null && selectedPost.plane_lon != null) {
      map.panTo({ lat: selectedPost.plane_lat, lng: selectedPost.plane_lon })
    }
  }, [map, selectedPost])

  return null
}
