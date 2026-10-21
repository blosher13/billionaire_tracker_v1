import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createDiscussionPost, getChallenge, getDiscussion, toggleReaction, voteOnIdea } from '../api'
import { solveChallenge } from '../botcheck'
import { postTime } from '../format'
import type { Board as BoardName, DiscussionPost, ReactionKind } from '../types'

const REACTIONS: { kind: ReactionKind; emoji: string; label: string }[] = [
  { kind: 'like', emoji: '👍', label: 'Like' },
  { kind: 'love', emoji: '❤️', label: 'Love' },
  { kind: 'haha', emoji: '😂', label: 'Haha' },
  { kind: 'wow', emoji: '😮', label: 'Wow' },
  { kind: 'sad', emoji: '😢', label: 'Sad' },
]
const MAX_BODY = 1000
const MAX_AUTHOR = 40

type Patch = (post: DiscussionPost) => DiscussionPost
type Check = { state: 'idle' | 'working' } | { state: 'done'; challenge: string; solution: number }

/** The visitors' boards under the map: open discussion on the left, feature ideas on the right. */
export function Discussion() {
  return (
    <section id="discussion" className="discussion" aria-label="Discussion">
      <p className="discussion-rules quiet">
        Text only: no links, photos, swear words or sexual language.
      </p>
      <div className="boards">
        <Board board="general" title="Discussion" />
        <Board board="ideas" title="What features would you like to see added to this app?" />
      </div>
    </section>
  )
}

function Board({ board, title }: { board: BoardName; title: string }) {
  const ideas = board === 'ideas'
  const [posts, setPosts] = useState<DiscussionPost[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    (offset: number) => {
      setLoading(true)
      getDiscussion(board, offset)
        .then((page) => {
          setPosts((prev) => (offset ? [...prev, ...page.posts] : page.posts))
          setHasMore(page.has_more)
          setTotal(page.total)
          setError(null)
        })
        .catch((err: Error) => setError(err.message))
        .finally(() => setLoading(false))
    },
    [board],
  )
  useEffect(() => load(0), [load])

  /** Apply a change to a post or to one of the replies. */
  const update = (id: number, patch: Patch) =>
    setPosts((prev) =>
      prev.map((p) =>
        p.id === id ? patch(p) : { ...p, replies: p.replies?.map((r) => (r.id === id ? patch(r) : r)) },
      ),
    )

  const added = (post: DiscussionPost) => {
    setTotal((n) => n + 1)
    // New ideas start at 0 votes, so show them at the top until the next reload re-sorts.
    if (ideas) setPosts((prev) => [post, ...prev])
    else if (!hasMore) setPosts((prev) => [...prev, post])
  }

  const composer = (
    <Composer
      board={board}
      parentId={null}
      title={ideas ? 'Suggest a feature' : 'Write a post'}
      placeholder={ideas ? 'I’d like to see…' : 'Where do you think they’re headed?'}
      onPosted={added}
    />
  )

  return (
    <div className={`board board-${board}`}>
      <header className="board-head">
        <h2>{title}</h2>
        <p className="quiet">
          {ideas
            ? `${total === 1 ? '1 idea' : `${total} ideas`}, most upvoted first. Vote on the ones you want.`
            : `${total === 1 ? '1 post' : `${total} posts`}, oldest first.`}
        </p>
      </header>

      {ideas && composer}
      {error && <p className="error">{error}</p>}
      {!loading && posts.length === 0 && !error && (
        <p className="hint">{ideas ? 'No ideas yet. Suggest the first one.' : 'No posts yet. Start the conversation below.'}</p>
      )}

      <ol className="talk">
        {posts.map((post) => (
          <PostItem key={post.id} post={post} ideas={ideas} update={update} onError={setError} />
        ))}
      </ol>

      {loading && <p className="quiet">Loading…</p>}
      {hasMore && !loading && (
        <button className="wide-button" onClick={() => load(posts.length)}>
          {ideas ? 'Show more ideas' : 'Show newer posts'}
        </button>
      )}

      {!ideas && composer}
    </div>
  )
}

function PostItem({
  post,
  ideas,
  update,
  onError,
}: {
  post: DiscussionPost
  ideas: boolean
  update: (id: number, patch: Patch) => void
  onError: (message: string) => void
}) {
  const [replying, setReplying] = useState(false)
  const replies = post.replies ?? []

  const react = (target: DiscussionPost, kind: ReactionKind) =>
    toggleReaction(target.id, kind)
      .then((r) => update(target.id, (p) => ({ ...p, ...r })))
      .catch((err: Error) => onError(err.message))

  const vote = (value: -1 | 1) =>
    voteOnIdea(post.id, post.my_vote === value ? 0 : value) // clicking your vote again takes it back
      .then((r) => update(post.id, (p) => ({ ...p, ...r })))
      .catch((err: Error) => onError(err.message))

  return (
    <li className={`talk-post${ideas ? ' talk-idea' : ''}`}>
      {ideas && <Votes post={post} onVote={vote} />}
      <div className="talk-main">
        <Meta post={post} />
        <p className="talk-body">{post.body}</p>
        <div className="talk-actions">
          {!ideas && <Reactions post={post} onReact={(kind) => react(post, kind)} />}
          <button className="text-button" onClick={() => setReplying((r) => !r)} aria-expanded={replying}>
            {replying ? 'Cancel reply' : 'Reply'}
          </button>
        </div>

        {(replies.length > 0 || replying) && (
          <ol className="talk-replies" aria-label={`Replies to ${post.author}`}>
            {replies.map((reply) => (
              <li key={reply.id} className="talk-reply">
                <Meta post={reply} />
                <p className="talk-body">{reply.body}</p>
                <Reactions post={reply} onReact={(kind) => react(reply, kind)} />
              </li>
            ))}
          </ol>
        )}
        {replying && (
          <Composer
            board={post.board}
            parentId={post.id}
            placeholder={`Reply to ${post.author}`}
            compact
            onPosted={(reply) => {
              update(post.id, (p) => ({ ...p, replies: [...(p.replies ?? []), reply] }))
              setReplying(false)
            }}
          />
        )}
      </div>
    </li>
  )
}

function Meta({ post }: { post: DiscussionPost }) {
  return (
    <p className="talk-meta">
      <strong>{post.author}</strong>
      <time dateTime={post.created_at}>{postTime(post.created_at)}</time>
    </p>
  )
}

function Votes({ post, onVote }: { post: DiscussionPost; onVote: (value: -1 | 1) => void }) {
  return (
    <div className="votes" role="group" aria-label={`Score ${post.score}`}>
      <button
        className="vote vote-up"
        aria-pressed={post.my_vote === 1}
        aria-label="Upvote"
        title="Upvote"
        onClick={() => onVote(1)}
      >
        ▲
      </button>
      <span className="vote-score" aria-hidden>
        {post.score}
      </span>
      <button
        className="vote vote-down"
        aria-pressed={post.my_vote === -1}
        aria-label="Downvote"
        title="Downvote"
        onClick={() => onVote(-1)}
      >
        ▼
      </button>
    </div>
  )
}

function Reactions({ post, onReact }: { post: DiscussionPost; onReact: (kind: ReactionKind) => void }) {
  return (
    <div className="reactions" role="group" aria-label="Reactions">
      {REACTIONS.map(({ kind, emoji, label }) => {
        const count = post.reactions[kind] ?? 0
        return (
          <button
            key={kind}
            className="reaction"
            aria-pressed={post.mine.includes(kind)}
            aria-label={`${label}${count ? `, ${count}` : ''}`}
            title={label}
            onClick={() => onReact(kind)}
          >
            <span aria-hidden>{emoji}</span>
            {count > 0 && <span className="reaction-count">{count}</span>}
          </button>
        )
      })}
    </div>
  )
}

function Composer({
  board,
  parentId,
  title,
  placeholder,
  compact = false,
  onPosted,
}: {
  board: BoardName
  parentId: number | null
  title?: string
  placeholder: string
  compact?: boolean
  onPosted: (post: DiscussionPost) => void
}) {
  const [author, setAuthor] = useState('')
  const [body, setBody] = useState('')
  const [website, setWebsite] = useState('') // honeypot: hidden from people
  const [check, setCheck] = useState<Check>({ state: 'idle' })
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [posted, setPosted] = useState(false)

  const verify = async () => {
    setCheck({ state: 'working' })
    setError(null)
    try {
      const { challenge, bits } = await getChallenge()
      setCheck({ state: 'done', challenge, solution: await solveChallenge(challenge, bits) })
    } catch (err) {
      setCheck({ state: 'idle' })
      setError(err instanceof Error ? err.message : 'The bot check failed. Try again.')
    }
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (check.state !== 'done') return
    setSending(true)
    setError(null)
    try {
      const post = await createDiscussionPost({
        board,
        parent_id: parentId,
        author,
        body,
        website,
        challenge: check.challenge,
        solution: check.solution,
      })
      onPosted(post)
      setBody('')
      setPosted(true)
      setCheck({ state: 'idle' }) // each check is good for one post
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Posting failed. Try again.'
      setError(message)
      if (/bot check|already used/i.test(message)) setCheck({ state: 'idle' })
    } finally {
      setSending(false)
    }
  }

  const tooLong = body.length > MAX_BODY
  const canPost = check.state === 'done' && body.trim().length >= 2 && !tooLong && !sending
  const verb = parentId ? 'Reply' : board === 'ideas' ? 'Suggest' : 'Post'

  return (
    <form className={`composer${compact ? ' composer-compact' : ''}`} onSubmit={submit} noValidate>
      {title && <h3>{title}</h3>}
      <label className="field">
        <span>Name (optional)</span>
        <input
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          maxLength={MAX_AUTHOR}
          placeholder="Anonymous"
          autoComplete="nickname"
        />
      </label>
      <label className="field">
        <span>{parentId ? 'Reply' : board === 'ideas' ? 'Idea' : 'Post'}</span>
        <textarea
          value={body}
          onChange={(e) => {
            setBody(e.target.value)
            setPosted(false)
          }}
          rows={compact ? 2 : 4}
          placeholder={placeholder}
        />
      </label>
      <p className={`composer-count${tooLong ? ' over' : ''}`}>
        {body.length} / {MAX_BODY}
      </p>

      {/* Bots fill in every field; people never see this one. */}
      <label className="honeypot" aria-hidden>
        Website
        <input tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
      </label>

      <div className="composer-actions">
        <button
          type="button"
          className={`bot-check bot-check-${check.state}`}
          onClick={verify}
          disabled={check.state !== 'idle'}
          aria-live="polite"
        >
          <span className="bot-check-box" aria-hidden>
            {check.state === 'done' ? '✓' : ''}
          </span>
          {check.state === 'idle' ? "Verify I'm human" : check.state === 'working' ? 'Checking…' : 'Verified'}
        </button>
        <button type="submit" className="primary-button" disabled={!canPost}>
          {sending ? 'Sending…' : verb}
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {posted && !error && !parentId && (
        <p className="notice-ok" role="status">
          {board === 'ideas' ? 'Idea added.' : 'Posted.'}
        </p>
      )}
    </form>
  )
}
