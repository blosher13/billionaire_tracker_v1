import { useCallback, useEffect, useRef, useState } from 'react'
import { getLatestAll, getPosts } from './api'
import type { ConnectionStatus, Flight, LiveMessage, Post } from './types'

const MAX_POSTS = 100

/**
 * Live data for every tracked aircraft over ONE WebSocket (/ws/all):
 * the latest flight record per aircraft, plus posts for the aircraft whose posts were loaded.
 * Loads the stored records from Postgres first, then applies live updates straight from Kafka
 * (flight.enriched and x.posts.located). Reconnects with exponential backoff.
 */
export function useFleet(enabled: boolean) {
  const [flights, setFlights] = useState<Record<string, Flight>>({})
  const [posts, setPosts] = useState<Record<string, Post[]>>({})
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const loadedPosts = useRef(new Set<string>())

  // Keep whichever record is newer, in case a live update beats the REST response.
  const applyFlight = useCallback((next: Flight) => {
    setFlights((prev) => (prev[next.hex] && prev[next.hex].ts > next.ts ? prev : { ...prev, [next.hex]: next }))
  }, [])

  // Merge by post id, newest first.
  const applyPosts = useCallback((hex: string, incoming: Post[]) => {
    setPosts((prev) => {
      const byId = new Map((prev[hex] ?? []).map((p) => [p.post_id, p]))
      for (const post of incoming) byId.set(post.post_id, post)
      const merged = [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, MAX_POSTS)
      return { ...prev, [hex]: merged }
    })
  }, [])

  /** Fetch an aircraft's stored posts once (live ones arrive over the socket afterwards). */
  const loadPosts = useCallback(
    (hex: string) => {
      if (loadedPosts.current.has(hex)) return
      loadedPosts.current.add(hex)
      getPosts(hex).then((stored) => applyPosts(hex, stored))
    },
    [applyPosts],
  )

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let attempt = 0

    getLatestAll().then((stored) => {
      if (!cancelled) stored.forEach(applyFlight)
    })

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${protocol}://${window.location.host}/ws/all`)
      setStatus('connecting')
      socket.onopen = () => {
        attempt = 0
        setStatus('live')
      }
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as LiveMessage
        if (message.type === 'flight') applyFlight(message.data)
        else applyPosts(message.data.hex, [message.data])
      }
      socket.onclose = () => {
        if (cancelled) return
        setStatus('offline')
        retryTimer = window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** attempt++))
      }
    }
    connect()

    return () => {
      cancelled = true
      window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [enabled, applyFlight, applyPosts])

  return { flights, posts, status, loadPosts }
}
