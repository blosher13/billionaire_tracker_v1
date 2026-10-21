import type { Aircraft, Post } from '../types'
import { PostCard } from './PostCard'

interface Props {
  aircraft: Aircraft[]
  /** Aircraft hex to filter by, or null for everyone. */
  filter: string | null
  onFilter: (hex: string | null) => void
  posts: Post[]
  hasMore: boolean
  loading: boolean
  onLoadMore: () => void
  selectedPostId: string | null
  onSelectPost: (post: Post) => void
}

/** Every post ever stored, newest first, with where the jet was when it was posted. */
export function PostArchive({ aircraft, filter, onFilter, posts, hasMore, loading, onLoadMore, selectedPostId, onSelectPost }: Props) {
  const byHex = new Map(aircraft.map((a) => [a.hex, a]))
  const posters = aircraft.filter((a) => a.x_username)

  return (
    <section className="history" aria-label="Post archive">
      <h2 className="day-title">Every post</h2>
      <p className="quiet">
        All stored posts, kept permanently, each placed where the poster's jet was at the time.
      </p>
      <label className="field">
        <span>Show posts from</span>
        <select value={filter ?? ''} onChange={(e) => onFilter(e.target.value || null)}>
          <option value="">Everyone</option>
          {posters.map((a) => (
            <option key={a.hex} value={a.hex}>
              {a.owner ?? a.hex}
            </option>
          ))}
        </select>
      </label>

      {posts.length === 0 && !loading ? (
        <p className="quiet">No posts stored yet.</p>
      ) : (
        <ol className="slips">
          {posts.map((post) => (
            <PostCard
              key={`${post.post_id}-${post.hex}`}
              post={post}
              aircraft={byHex.get(post.hex)}
              selected={post.post_id === selectedPostId}
              onSelect={onSelectPost}
              showAuthor
            />
          ))}
        </ol>
      )}
      {loading && <p className="quiet">Loading posts…</p>}
      {hasMore && !loading && (
        <button className="wide-button" onClick={onLoadMore}>
          Show older posts
        </button>
      )}
    </section>
  )
}
