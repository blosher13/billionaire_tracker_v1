import { compact, initials, num, offsetLabel, postTime } from '../format'
import type { Aircraft, Post } from '../types'

interface Props {
  post: Post
  /** The tracked aircraft the post was matched to (for the owner's photo), if still tracked. */
  aircraft: Aircraft | undefined
  selected: boolean
  onSelect: (post: Post) => void
  /** Show who posted (lists that mix several people). */
  showAuthor?: boolean
}

const POST_TYPE_LABEL = { post: null, quote: 'Quote', reply: 'Reply' } as const

/** One post: when, what, and where the poster's jet was at that moment. */
export function PostCard({ post, aircraft, selected, onSelect, showAuthor = false }: Props) {
  const typeLabel = POST_TYPE_LABEL[post.post_type]

  return (
    <li className={`slip${selected ? ' slip-selected' : ''}`}>
      {showAuthor && (
        <div className="slip-author">
          {aircraft?.icon_url ? (
            <img className="slip-avatar" src={aircraft.icon_url} alt="" />
          ) : (
            <span className="slip-avatar strip-initials">{initials(post.owner, post.username)}</span>
          )}
          <strong>{post.owner ?? aircraft?.owner ?? `@${post.username}`}</strong>
        </div>
      )}
      <p className="slip-time">
        <time dateTime={post.created_at}>{postTime(post.created_at)}</time>
        {typeLabel && <span className="slip-tag">{typeLabel}</span>}
      </p>
      <p className="slip-text">{post.text}</p>
      <PostLocation post={post} onShow={() => onSelect(post)} />
      <p className="slip-stats">
        <span>{compact(post.like_count)} likes</span>
        <span>{compact(post.repost_count)} reposts</span>
        <span>{compact(post.reply_count)} replies</span>
        <a href={post.url} target="_blank" rel="noreferrer">
          Open on X
        </a>
      </p>
    </li>
  )
}

/** Where the jet was when the post was made, or why that's unknown. */
export function PostLocation({ post, onShow }: { post: Post; onShow: () => void }) {
  if (post.location_status === 'unknown' || post.plane_lat == null || post.plane_lon == null) {
    return <p className="slip-where slip-where-unknown">Jet location unknown: no position recorded for this jet yet.</p>
  }

  const state = post.plane_on_ground
    ? 'on the ground'
    : post.plane_altitude_ft != null
      ? `at ${num(post.plane_altitude_ft)} ft`
      : null
  const age = post.position_offset_s != null ? offsetLabel(post.position_offset_s) : null

  return (
    <p className="slip-where">
      <button className="where-button" onClick={onShow}>
        Jet at {post.plane_lat.toFixed(3)}, {post.plane_lon.toFixed(3)}
        {state && ` ${state}`}
      </button>
      <span className="slip-where-note">
        {post.location_status === 'matched'
          ? `Position recorded ${age} the post.`
          : post.location_status === 'nearest'
            ? `Tracking began after this post; closest recorded position, ${age} the post.`
            : `Last known position, recorded ${age} the post.`}
      </span>
    </p>
  )
}
