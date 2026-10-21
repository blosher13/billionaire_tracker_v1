import { useCallback, useEffect, useMemo, useState } from 'react'
import { getConfig, getHistoryDay, getHistoryDays, getPostArchive, listAircraft } from './api'
import { Discussion } from './components/Discussion'
import { Dossier } from './components/Dossier'
import { FlightMap } from './components/FlightMap'
import { HistoryMap } from './components/HistoryMap'
import { HistoryPanel } from './components/HistoryPanel'
import { PostArchive } from './components/PostArchive'
import { PostsPanel } from './components/PostsPanel'
import { StripBay } from './components/StripBay'
import { UpdatesBar } from './components/UpdatesBar'
import type { Aircraft, ConnectionStatus, HistoryDay, HistoryDaySummary, Post } from './types'
import { useFleet } from './useFleet'
import './App.css'

type Mode = 'live' | 'history'
type HistoryView = 'day' | 'archive'

// One colour per person: their strip edge, map ring and history track. Magenta is kept for posts.
const PERSON_COLORS = ['#2d5c96', '#1f7a75', '#b7791f', '#6b4fa0', '#b5462b', '#5e7a2a', '#3e7cb1', '#7a5c3e']

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  live: 'Live',
  connecting: 'Connecting',
  offline: 'Reconnecting',
}

export default function App() {
  const [mode, setMode] = useState<Mode>('live')
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [readOnly, setReadOnly] = useState(true)
  const [xPaused, setXPaused] = useState(false)
  const [aircraft, setAircraft] = useState<Aircraft[]>([])
  // null = overview: every billionaire on the map, nobody selected.
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedPost, setSelectedPost] = useState<Post | null>(null)

  // Live mode: one WebSocket for the whole fleet.
  const { flights, posts: postsByHex, status, loadPosts } = useFleet(mode === 'live')
  const info = aircraft.find((a) => a.hex === selected)
  const flight = selected ? (flights[selected] ?? null) : null
  const posts = selected ? (postsByHex[selected] ?? []) : []

  useEffect(() => {
    if (selected) loadPosts(selected)
  }, [selected, loadPosts])

  // History mode.
  const [days, setDays] = useState<HistoryDaySummary[]>([])
  const [historyDate, setHistoryDate] = useState<string | null>(null)
  const [historyData, setHistoryData] = useState<HistoryDay | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyView, setHistoryView] = useState<HistoryView>('day')

  // Post archive: every stored post, any age, paged 50 at a time.
  const [archiveFilter, setArchiveFilter] = useState<string | null>(null)
  const [archivePosts, setArchivePosts] = useState<Post[]>([])
  const [archiveHasMore, setArchiveHasMore] = useState(false)
  const [archiveLoading, setArchiveLoading] = useState(false)

  const colors = useMemo(() => {
    const hexes = new Set([...aircraft.map((a) => a.hex), ...(historyData?.end_of_day.map((p) => p.hex) ?? [])])
    return Object.fromEntries([...hexes].sort().map((hex, i) => [hex, PERSON_COLORS[i % PERSON_COLORS.length]]))
  }, [aircraft, historyData])

  // Selecting the selected billionaire again goes back to the overview.
  const selectAircraft = useCallback((hex: string) => {
    setSelected((current) => (current === hex ? null : hex))
    setSelectedPost(null)
  }, [])
  const showAll = useCallback(() => {
    setSelected(null)
    setSelectedPost(null)
  }, [])

  const refreshAircraft = useCallback(() => {
    listAircraft()
      .then((list) => {
        setAircraft(list)
        setSelected((current) => (current && list.some((a) => a.hex === current) ? current : null))
      })
      .catch((err: Error) => setError(err.message))
  }, [])

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        setApiKey(cfg.google_maps_api_key)
        setReadOnly(cfg.read_only)
        setXPaused(cfg.x_paused)
      })
      .catch((err: Error) => setError(err.message))
    refreshAircraft()
  }, [refreshAircraft])

  // Opening History: load the day list and pick the most recent day that has posts (else today).
  useEffect(() => {
    if (mode !== 'history') return
    getHistoryDays()
      .then(({ days }) => {
        setDays(days)
        setHistoryDate((current) => current ?? days.find((d) => d.posts > 0)?.date ?? days[0]?.date ?? null)
      })
      .catch((err: Error) => setError(err.message))
  }, [mode])

  useEffect(() => {
    if (mode !== 'history' || !historyDate) return
    let cancelled = false
    setHistoryLoading(true)
    getHistoryDay(historyDate)
      .then((data) => !cancelled && setHistoryData(data))
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setHistoryLoading(false))
    return () => {
      cancelled = true
    }
  }, [mode, historyDate])

  // (Re)load the archive's first page when it's opened or the person filter changes.
  useEffect(() => {
    if (mode !== 'history' || historyView !== 'archive') return
    let cancelled = false
    setArchiveLoading(true)
    getPostArchive(archiveFilter, null)
      .then(({ posts, has_more }) => {
        if (cancelled) return
        setArchivePosts(posts)
        setArchiveHasMore(has_more)
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setArchiveLoading(false))
    return () => {
      cancelled = true
    }
  }, [mode, historyView, archiveFilter])

  const loadOlderPosts = () => {
    const oldest = archivePosts[archivePosts.length - 1]
    if (!oldest) return
    setArchiveLoading(true)
    getPostArchive(archiveFilter, oldest.created_at)
      .then(({ posts, has_more }) => {
        setArchivePosts((prev) => [...prev, ...posts])
        setArchiveHasMore(has_more)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setArchiveLoading(false))
  }

  // The history map takes one day's data; the archive shows just its post pins.
  const archiveMapData = useMemo<HistoryDay>(
    () => ({
      date: '',
      timezone: '',
      posts: archivePosts,
      tracks: {},
      end_of_day: [],
    }),
    [archivePosts],
  )

  const switchMode = (next: Mode) => {
    setMode(next)
    setSelectedPost(null)
  }
  const switchHistoryView = (next: HistoryView) => {
    setHistoryView(next)
    setSelectedPost(null)
  }
  const selectDate = (date: string) => {
    setHistoryDate(date)
    setSelectedPost(null)
  }

  // Adds/removes go through Kafka, so the API's view updates a moment later.
  const handleChanged = useCallback(() => window.setTimeout(refreshAircraft, 1000), [refreshAircraft])

  return (
    <>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <h1>BillionTracker</h1>
            <p>Billionaires' private jets, tracked from public ADS-B signals.</p>
          </div>
          <nav className="modes" aria-label="View">
            <button aria-pressed={mode === 'live'} onClick={() => switchMode('live')}>
              Now
            </button>
            <button aria-pressed={mode === 'history'} onClick={() => switchMode('history')}>
              History
            </button>
          </nav>
          <a className="topbar-link" href="#discussion">
            Discussion
          </a>
          {mode === 'live' && (
            <p className={`status status-${status}`} role="status">
              <span className="status-dot" aria-hidden />
              {STATUS_LABEL[status]}
            </p>
          )}
        </header>
        <UpdatesBar />

        <aside className="sidebar">
          {error && <p className="error">Can't reach the server: {error}</p>}

          {mode === 'live' ? (
            <>
              <StripBay
                aircraft={aircraft}
                selected={selected}
                flights={flights}
                colors={colors}
                readOnly={readOnly}
                onSelect={selectAircraft}
                onShowAll={showAll}
                onChanged={handleChanged}
              />
              {selected ? (
                <>
                  <Dossier aircraft={info} flight={flight} />
                  <PostsPanel
                    aircraft={info}
                    posts={posts}
                    paused={xPaused}
                    selectedPostId={selectedPost?.post_id ?? null}
                    onSelectPost={setSelectedPost}
                  />
                </>
              ) : (
                <p className="hint">
                  Pick a strip, or a photo on the map, to see that jet's flight, weather and posts.
                </p>
              )}
            </>
          ) : (
            <>
              <nav className="subtabs" aria-label="History view">
                <button aria-pressed={historyView === 'day'} onClick={() => switchHistoryView('day')}>
                  By day
                </button>
                <button aria-pressed={historyView === 'archive'} onClick={() => switchHistoryView('archive')}>
                  Every post
                </button>
              </nav>
              {historyView === 'archive' ? (
                <PostArchive
                  aircraft={aircraft}
                  filter={archiveFilter}
                  onFilter={(hex) => {
                    setArchiveFilter(hex)
                    setSelectedPost(null)
                  }}
                  posts={archivePosts}
                  hasMore={archiveHasMore}
                  loading={archiveLoading}
                  onLoadMore={loadOlderPosts}
                  selectedPostId={selectedPost?.post_id ?? null}
                  onSelectPost={setSelectedPost}
                />
              ) : (
                <HistoryPanel
                  days={days}
                  date={historyDate}
                  onSelectDate={selectDate}
                  data={historyData}
                  loading={historyLoading}
                  aircraft={aircraft}
                  colors={colors}
                  selectedPostId={selectedPost?.post_id ?? null}
                  onSelectPost={setSelectedPost}
                />
              )}
            </>
          )}
        </aside>

        <main className="map-container">
          {!apiKey ? (
            <div className="map-empty">
              {apiKey === '' ? 'The map needs google_maps_api_key in .env.' : 'Loading the map…'}
            </div>
          ) : mode === 'live' ? (
            <FlightMap
              apiKey={apiKey}
              aircraft={aircraft}
              flights={flights}
              colors={colors}
              selectedHex={selected}
              onSelectAircraft={selectAircraft}
              posts={posts}
              selectedPost={selectedPost}
              onSelectPost={setSelectedPost}
            />
          ) : (
            <HistoryMap
              apiKey={apiKey}
              data={historyView === 'archive' ? archiveMapData : historyData}
              aircraft={aircraft}
              colors={colors}
              selectedPost={selectedPost}
              onSelectPost={setSelectedPost}
            />
          )}
        </main>
      </div>
      <Discussion />
    </>
  )
}
