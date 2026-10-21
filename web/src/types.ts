/** An aircraft from tracked.aircraft (GET /api/aircraft). */
export interface Aircraft {
  hex: string
  owner: string | null
  registration: string | null
  aircraft_type: string | null
  x_username: string | null
  icon_url: string | null
  /** Photo attribution, e.g. 'Photo: Jane Doe, CC BY-SA 4.0' */
  icon_credit: string | null
  icon_source_url: string | null
}

/** One x.posts.located record: an X post plus the aircraft's position at the time. */
export interface Post {
  post_id: string
  hex: string
  username: string
  owner: string | null
  text: string
  post_type: 'post' | 'reply' | 'quote'
  url: string
  like_count: number | null
  reply_count: number | null
  repost_count: number | null
  view_count: number | null
  created_at: string // ISO-8601 UTC
  /**
   * matched = position observed ≤60 min before the post; last_known = older (assumed still there);
   * nearest = no position before the post (it predates tracking), so the closest recorded one; unknown = none at all
   */
  location_status: 'matched' | 'last_known' | 'nearest' | 'unknown'
  plane_lat: number | null
  plane_lon: number | null
  plane_altitude_ft: number | null
  plane_on_ground: boolean | null
  plane_ts: string | null
  /** post time minus position time, seconds: how old the position was when the post was made */
  position_offset_s: number | null
}

export type LiveMessage = { type: 'flight'; data: Flight } | { type: 'post'; data: Post }

/** One flight.enriched record (from Postgres via REST, or live over the WebSocket). */
export interface Flight {
  hex: string
  owner: string | null
  registration: string | null
  flight: string | null
  lat: number
  lon: number
  altitude_ft: number | null
  ground_speed_kt: number | null
  track_deg: number | null
  on_ground: boolean
  ts: string // ISO-8601 UTC
  temp_c: number | null
  feels_like_c: number | null
  humidity_pct: number | null
  weather_description: string | null
  wind_speed_ms: number | null
  wind_deg: number | null
  weather_ts: string | null
}

export type ConnectionStatus = 'connecting' | 'live' | 'offline'

/** GET /api/history/days: one entry per day, newest first. */
export interface HistoryDaySummary {
  date: string // YYYY-MM-DD in the viewer's time zone
  posts: number
  positions: number
}

export interface TrackPoint {
  ts: string
  lat: number
  lon: number
  altitude_ft: number | null
  ground_speed_kt: number | null
  track_deg: number | null
  on_ground: boolean
}

/** Where an aircraft was as of the end of the day (possibly from an earlier day). */
export interface EndOfDayPosition extends TrackPoint {
  hex: string
  owner: string | null
  registration: string | null
}

/** GET /api/history/{date}: everything for one day. */
export interface HistoryDay {
  date: string
  timezone: string
  posts: Post[]
  tracks: Record<string, TrackPoint[]>
  end_of_day: EndOfDayPosition[]
}

export type ReactionKind = 'like' | 'love' | 'haha' | 'wow' | 'sad'

export type Board = 'general' | 'ideas'

/** A visitor's post (or reply) on a discussion board. */
export interface DiscussionPost {
  id: number
  board: Board
  /** Set on replies: the post being replied to. */
  parent_id: number | null
  author: string
  body: string
  created_at: string
  reactions: Partial<Record<ReactionKind, number>>
  /** The reactions this visitor gave. */
  mine: ReactionKind[]
  /** Feature ideas: upvotes minus downvotes, and this visitor's vote (1, -1 or 0). */
  score: number
  my_vote: -1 | 0 | 1
  /** Top-level posts only: the replies, oldest first. */
  replies?: DiscussionPost[]
}
