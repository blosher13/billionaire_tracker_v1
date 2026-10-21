"""History: browse the last HISTORY_DAYS days of posts and flight paths, day by day.

Everything comes from Postgres, which keeps every enriched position and located post
(Kafka itself only retains about 7 days). Days are calendar days in the visitor's time zone,
passed by the browser as an IANA name (e.g. "America/Los_Angeles").

Timestamps are stored as UTC in timestamp-without-time-zone columns, so day boundaries are
converted to naive UTC before querying.
"""

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request

from api.live import jsonable
from api.posts import PLACED_POSTS

HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "30"))
# Nothing before this date is shown: history begins when tracking began. Default: no limit.
HISTORY_START_DATE = date.fromisoformat(os.getenv("HISTORY_START_DATE", "2000-01-01"))
MAX_TRACK_POINTS = 1000  # per aircraft per day; longer tracks are thinned evenly

router = APIRouter(prefix="/api/history")


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, f"unknown time zone: {tz}")


def _utc_naive(local: datetime) -> datetime:
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return _utc_naive(start), _utc_naive(end)


def _first_day(today: date) -> date:
    """Oldest day visitors can open: within the last HISTORY_DAYS, and not before HISTORY_START_DATE."""
    return max(today - timedelta(days=HISTORY_DAYS - 1), HISTORY_START_DATE)


def _query(request: Request, sql: str, params: tuple | dict) -> list[dict]:
    with request.app.state.db.connection() as conn:
        try:
            return conn.execute(sql, params).fetchall()
        except psycopg.errors.UndefinedTable:
            return []  # the JDBC Sink creates tables on their first write


def _thin(points: list[dict], limit: int) -> list[dict]:
    """Keep at most `limit` points, evenly spaced, always including the last one."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    thinned = [points[int(i * step)] for i in range(limit - 1)]
    return thinned + [points[-1]]


@router.get("/days")
def days(request: Request, tz: str = Query("UTC", max_length=64)) -> dict:
    """Days from the first available day up to today (newest first), with post/position counts."""
    zone = _zone(tz)
    today = datetime.now(zone).date()
    first = _first_day(today)
    since, _ = _day_bounds(first, zone)

    def counts(table: str, column: str) -> dict[date, int]:
        rows = _query(
            request,
            f"SELECT ({column} AT TIME ZONE 'UTC' AT TIME ZONE %s)::date AS day, count(*) AS n "
            f"FROM {table} WHERE {column} >= %s GROUP BY 1",
            (tz, since),
        )
        return {r["day"]: r["n"] for r in rows}

    posts = counts("x_posts", "created_at")
    positions = counts("flight_enriched", "ts")
    return {
        "today": today.isoformat(),
        "days": [
            {"date": d.isoformat(), "posts": posts.get(d, 0), "positions": positions.get(d, 0)}
            for d in (today - timedelta(days=i) for i in range(max((today - first).days + 1, 1)))
        ],
    }


@router.get("/posts")
def post_archive(
    request: Request,
    hex: str | None = Query(None, pattern="^[0-9a-fA-F]{6}$"),
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Every stored post (any age), newest first, each with where the jet was when it was posted.
    Filter by aircraft with `hex`; page back with `before` = the oldest created_at already shown."""
    if before is not None and before.tzinfo is not None:
        before = _utc_naive(before)  # columns are naive UTC
    rows = _query(
        request,
        f"SELECT * FROM {PLACED_POSTS} WHERE (%(hex)s::text IS NULL OR hex = %(hex)s) "
        "AND (%(before)s::timestamp IS NULL OR created_at < %(before)s) "
        "ORDER BY created_at DESC LIMIT %(limit)s",
        {"hex": hex.lower() if hex else None, "before": before, "limit": limit + 1},
    )
    return {"posts": [jsonable(r) for r in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/{day}")
def history_day(request: Request, day: date, tz: str = Query("UTC", max_length=64)) -> dict:
    """Everything for one day: posts (with the jet's position at the time), each jet's flight
    path that day, and where each jet was at the end of the day."""
    zone = _zone(tz)
    today = datetime.now(zone).date()
    if not _first_day(today) <= day <= today:
        raise HTTPException(422, f"history covers {_first_day(today)} to {today} only")
    start, end = _day_bounds(day, zone)

    posts = _query(
        request,
        f"SELECT * FROM {PLACED_POSTS} WHERE created_at >= %s AND created_at < %s ORDER BY created_at",
        (start, end),
    )

    track_rows = _query(
        request,
        "SELECT hex, ts, lat, lon, altitude_ft, ground_speed_kt, track_deg, on_ground FROM flight_enriched "
        "WHERE ts >= %s AND ts < %s ORDER BY hex, ts",
        (start, end),
    )
    tracks: dict[str, list[dict]] = {}
    for row in track_rows:
        tracks.setdefault(row.pop("hex"), []).append(jsonable(row))

    # Last known position of every aircraft as of the end of the day (it may be from an
    # earlier day, e.g. when the jet sat parked all day).
    end_rows = _query(
        request,
        "SELECT DISTINCT ON (hex) hex, owner, registration, ts, lat, lon, altitude_ft, track_deg, on_ground "
        "FROM flight_enriched WHERE ts < %s ORDER BY hex, ts DESC",
        (end,),
    )

    return {
        "date": day.isoformat(),
        "timezone": tz,
        "posts": [jsonable(p) for p in posts],
        "tracks": {h: _thin(points, MAX_TRACK_POINTS) for h, points in tracks.items()},
        "end_of_day": [jsonable(r) for r in end_rows],
    }
