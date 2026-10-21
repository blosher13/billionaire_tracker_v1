import type { CSSProperties } from 'react'
import type { Aircraft, HistoryDay, HistoryDaySummary, Post } from '../types'
import { PostCard } from './PostCard'

interface Props {
  days: HistoryDaySummary[] // newest first
  date: string | null
  onSelectDate: (date: string) => void
  data: HistoryDay | null
  loading: boolean
  aircraft: Aircraft[]
  colors: Record<string, string>
  selectedPostId: string | null
  onSelectPost: (post: Post) => void
}

const longDate = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
const shortDate = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })

/** One day at a time: where each jet flew, and who posted what from where. */
export function HistoryPanel({ days, date, onSelectDate, data, loading, aircraft, colors, selectedPostId, onSelectPost }: Props) {
  const index = days.findIndex((d) => d.date === date)
  const older = index >= 0 ? days[index + 1] : undefined
  const newer = index > 0 ? days[index - 1] : undefined
  const byHex = new Map(aircraft.map((a) => [a.hex, a]))

  return (
    <section className="history" aria-label="History by day">
      <div className="day-picker">
        <button className="day-step" onClick={() => older && onSelectDate(older.date)} disabled={!older} aria-label="Previous day">
          ‹
        </button>
        <h2 className="day-title">{date ? longDate(date) : 'History'}</h2>
        <button className="day-step" onClick={() => newer && onSelectDate(newer.date)} disabled={!newer} aria-label="Next day">
          ›
        </button>
      </div>

      <DayStrip days={days} date={date} onSelectDate={onSelectDate} />
      {days.length > 0 && (
        <p className="quiet">
          Tracking began {shortDate(days[days.length - 1].date)}. Up to 30 days are kept here; older posts are in the
          archive.
        </p>
      )}

      {loading && <p className="quiet">Loading the day…</p>}
      {!loading && data && (
        <>
          <h3 className="subhead">Jets</h3>
          <ul className="legend">
            {data.end_of_day.map((p) => {
              const info = byHex.get(p.hex)
              const points = data.tracks[p.hex]?.length ?? 0
              return (
                <li key={p.hex} style={{ '--strip-color': colors[p.hex] } as CSSProperties}>
                  <span className="legend-line" aria-hidden />
                  <span>
                    <strong>{info?.owner ?? p.owner ?? p.hex}</strong>
                    <span className="quiet">
                      {points > 1
                        ? ` flew, ${points} positions recorded`
                        : points === 1
                          ? ' 1 position recorded'
                          : ' stayed put, no new positions'}
                    </span>
                  </span>
                </li>
              )
            })}
          </ul>

          <h3 className="subhead">Posts</h3>
          {data.posts.length === 0 ? (
            <p className="quiet">Nobody's top post fell on this day.</p>
          ) : (
            <ol className="slips">
              {data.posts.map((post) => (
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
        </>
      )}
    </section>
  )
}

/** One cell per day, oldest to newest: filled = has posts, outlined = has flight data. */
function DayStrip({ days, date, onSelectDate }: { days: HistoryDaySummary[]; date: string | null; onSelectDate: (d: string) => void }) {
  return (
    <div className="day-strip">
      {[...days].reverse().map((d) => (
        <button
          key={d.date}
          title={`${shortDate(d.date)}: ${d.posts} posts, ${d.positions} positions`}
          className={['day-cell', d.posts ? 'has-posts' : '', d.positions ? 'has-positions' : '', d.date === date ? 'current' : '']
            .filter(Boolean)
            .join(' ')}
          onClick={() => onSelectDate(d.date)}
          aria-label={shortDate(d.date)}
          aria-current={d.date === date ? 'date' : undefined}
        />
      ))}
    </div>
  )
}
