import type { Aircraft, Post } from '../types'
import { PostCard } from './PostCard'

interface Props {
  aircraft: Aircraft | undefined
  posts: Post[]
  /** New posts aren't being fetched (X credits used up); the stored ones still show. */
  paused: boolean
  selectedPostId: string | null
  onSelectPost: (post: Post) => void
}

/** The selected billionaire's top posts, each placed where their jet was at the time. */
export function PostsPanel({ aircraft, posts, paused, selectedPostId, onSelectPost }: Props) {
  if (!aircraft?.x_username) return null

  return (
    <section className="posts-section" aria-label="Posts">
      <div className="section-head">
        <h3>Top posts</h3>
        <a href={`https://x.com/${aircraft.x_username}`} target="_blank" rel="noreferrer">
          @{aircraft.x_username}
        </a>
      </div>
      <p className="quiet">
        The most-liked, reposted and replied-to original post from each day, placed where the jet was when it
        was posted.
      </p>
      {paused && (
        <p className="notice">New posts are paused for now. The latest one picked is shown below.</p>
      )}

      {posts.length === 0 ? (
        <p className="quiet">
          {paused ? 'No posts were picked before updates paused.' : 'No posts yet. The next one is picked at midnight.'}
        </p>
      ) : (
        <ol className="slips">
          {posts.map((post) => (
            <PostCard
              key={post.post_id}
              post={post}
              aircraft={aircraft}
              selected={post.post_id === selectedPostId}
              onSelect={onSelectPost}
            />
          ))}
        </ol>
      )}
    </section>
  )
}
