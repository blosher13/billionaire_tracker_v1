import { AdvancedMarker, InfoWindow } from '@vis.gl/react-google-maps'
import type { KeyboardEvent } from 'react'
import { compact, offsetLabel, postTime } from '../format'
import type { Post } from '../types'

interface Props {
  posts: Post[]
  selectedPost: Post | null
  onSelectPost: (post: Post | null) => void
}

const TYPE_LABEL = { post: 'Post', quote: 'Quote', reply: 'Reply' } as const
// The pin sits up and to the right of its point (CSS .post-pin), so it doesn't cover the jet's
// photo and name; the popup opens above the pin.
const PIN_OFFSET_POPUP: [number, number] = [34, -78]

/** Where the jet was, in a few words. */
function whereNote(post: Post): string {
  const age = post.position_offset_s != null ? offsetLabel(post.position_offset_s) : null
  if (post.location_status === 'nearest')
    return `Tracking began after this post; the jet's closest recorded position${age ? `, ${age} the post` : ''}.`
  return post.location_status === 'matched'
    ? `Jet's position, recorded ${age ?? 'around the time of'} the post.`
    : `Jet's last known position${age ? `, recorded ${age} the post` : ''}.`
}

interface PinGroup {
  key: string
  lat: number
  lng: number
  /** Newest first. */
  posts: Post[]
}

/**
 * Posts made from (nearly) the same place share one pin: a parked jet gives every post the same
 * position. Coordinates are rounded to 3 decimals (about 100 m), so posts from one airport group
 * together while posts along a flight stay apart.
 */
export function groupByLocation(posts: Post[]): PinGroup[] {
  const groups = new Map<string, PinGroup>()
  for (const post of posts) {
    if (post.plane_lat == null || post.plane_lon == null) continue
    const key = `${post.plane_lat.toFixed(3)},${post.plane_lon.toFixed(3)}`
    const group = groups.get(key) ?? { key, lat: post.plane_lat, lng: post.plane_lon, posts: [] }
    if (!group.posts.some((p) => p.post_id === post.post_id)) group.posts.push(post)
    groups.set(key, group)
  }
  for (const group of groups.values()) group.posts.sort((a, b) => b.created_at.localeCompare(a.created_at))
  return [...groups.values()]
}

/** One 𝕏 pin per location (with a count when several posts share it) and a popup that cycles through them. */
export function PostPins({ posts, selectedPost, onSelectPost }: Props) {
  const groups = groupByLocation(posts)
  const open = selectedPost ? groups.find((g) => g.posts.some((p) => p.post_id === selectedPost.post_id)) : undefined
  const index = open ? open.posts.findIndex((p) => p.post_id === selectedPost!.post_id) : -1

  const step = (by: number) => {
    if (!open) return
    onSelectPost(open.posts[(index + by + open.posts.length) % open.posts.length])
  }
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft') step(-1)
    if (e.key === 'ArrowRight') step(1)
  }

  return (
    <>
      {groups.map((group) => {
        const count = group.posts.length
        const active = group === open
        return (
          <AdvancedMarker
            key={group.key}
            position={{ lat: group.lat, lng: group.lng }}
            title={count > 1 ? `${count} posts from here` : group.posts[0].text.slice(0, 80)}
            onClick={() => onSelectPost(active ? null : group.posts[0])}
            zIndex={active ? 2100 : 2000}
          >
            <div className={`post-pin${active ? ' active' : ''}`}>
              𝕏{count > 1 && <span className="post-pin-count">{count}</span>}
            </div>
          </AdvancedMarker>
        )
      })}

      {open && selectedPost && (
        <InfoWindow
          // Anchored to the pin (not the post), so cycling doesn't move the popup.
          position={{ lat: open.lat, lng: open.lng }}
          pixelOffset={PIN_OFFSET_POPUP}
          onCloseClick={() => onSelectPost(null)}
          headerContent={
            open.posts.length > 1 ? (
              <div className="info-pager" role="group" aria-label="Posts from this location" onKeyDown={onKeyDown}>
                <button onClick={() => step(-1)} aria-label="Newer post" title="Newer post">
                  ‹
                </button>
                <span aria-live="polite">
                  Post {index + 1} of {open.posts.length} from here
                </span>
                <button onClick={() => step(1)} aria-label="Older post" title="Older post">
                  ›
                </button>
              </div>
            ) : (
              <span className="info-pager-single">1 post from here</span>
            )
          }
        >
          <article className="info-post" onKeyDown={onKeyDown}>
            <header className="info-meta">
              <strong>{selectedPost.owner ?? `@${selectedPost.username}`}</strong>
              <span>
                @{selectedPost.username},{' '}
                <time dateTime={selectedPost.created_at}>{postTime(selectedPost.created_at)}</time>
                {selectedPost.post_type !== 'post' && (
                  <span className="info-tag">{TYPE_LABEL[selectedPost.post_type]}</span>
                )}
              </span>
            </header>
            <p className="info-text">{selectedPost.text}</p>
            <p className="info-where">{whereNote(selectedPost)}</p>
            <footer className="info-foot">
              <span>
                {compact(selectedPost.like_count)} likes, {compact(selectedPost.repost_count)} reposts
              </span>
              <a href={selectedPost.url} target="_blank" rel="noreferrer">
                View on X ↗
              </a>
            </footer>
          </article>
        </InfoWindow>
      )}
    </>
  )
}
