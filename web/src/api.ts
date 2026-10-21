import type {
  Aircraft,
  Board,
  DiscussionPost,
  Flight,
  HistoryDay,
  HistoryDaySummary,
  Post,
  ReactionKind,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

export const getConfig = () => request<{ google_maps_api_key: string; read_only: boolean; x_paused: boolean }>('/api/config')

export const listAircraft = () => request<Aircraft[]>('/api/aircraft')

export const addAircraft = (hex: string, owner: string) =>
  request<Aircraft>('/api/aircraft', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hex, owner: owner || null }),
  })

export const removeAircraft = (hex: string) =>
  request<void>(`/api/aircraft/${hex}`, { method: 'DELETE' })

/** Stored X posts for the aircraft's owner, newest first (empty if none yet). */
export const getPosts = (hex: string) => request<Post[]>(`/api/aircraft/${hex}/posts`).catch(() => [] as Post[])

/** Latest stored record, or null if the pipeline hasn't produced one for this aircraft yet. */
export async function getLatest(hex: string): Promise<Flight | null> {
  try {
    return await request<Flight>(`/api/aircraft/${hex}/latest`)
  } catch {
    return null
  }
}

/** The viewer's IANA time zone, so history days match their calendar. */
export const viewerTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

export const getHistoryDays = () =>
  request<{ today: string; days: HistoryDaySummary[] }>(`/api/history/days?tz=${encodeURIComponent(viewerTimeZone())}`)

export const getHistoryDay = (date: string) =>
  request<HistoryDay>(`/api/history/${date}?tz=${encodeURIComponent(viewerTimeZone())}`)

/** Latest record for every tracked aircraft (homepage fleet map). */
export const getLatestAll = () => request<Flight[]>('/api/latest').catch(() => [] as Flight[])

/** Every stored post (any age), newest first. Page back by passing the oldest created_at shown. */
export const getPostArchive = (hex: string | null, before: string | null) => {
  const params = new URLSearchParams({ limit: '50' })
  if (hex) params.set('hex', hex)
  if (before) params.set('before', before)
  return request<{ posts: Post[]; has_more: boolean }>(`/api/history/posts?${params}`)
}

// --- discussion ---------------------------------------------------------------------

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** A page of a board's posts with their replies (general: oldest first; ideas: top-voted first). */
export const getDiscussion = (board: Board, offset: number) =>
  request<{ posts: DiscussionPost[]; has_more: boolean; total: number }>(
    `/api/discussion?board=${board}&offset=${offset}`,
  )

export const getChallenge = () => request<{ challenge: string; bits: number }>('/api/discussion/challenge')

export const createDiscussionPost = (post: {
  board: Board
  parent_id: number | null
  author: string
  body: string
  website: string
  challenge: string
  solution: number
}) => request<DiscussionPost>('/api/discussion', json(post))

export const toggleReaction = (id: number, kind: ReactionKind) =>
  request<Pick<DiscussionPost, 'reactions' | 'mine'>>(`/api/discussion/${id}/reactions`, json({ kind }))

/** 1 = upvote, -1 = downvote, 0 = take the vote back. */
export const voteOnIdea = (id: number, value: -1 | 0 | 1) =>
  request<Pick<DiscussionPost, 'score' | 'my_vote'>>(`/api/discussion/${id}/vote`, json({ value }))

export interface UpdateStatus {
  positions_updated_at: string | null
  next_positions_at: string | null
  x_updated_at: string | null
  next_x_at: string | null
  x_paused: boolean
}

export const getStatus = () => request<UpdateStatus>('/api/status')
