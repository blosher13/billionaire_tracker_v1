"""Discussion boards: visitors write text posts, reply to them, and react.

Two boards: "general" (oldest first, with reactions) and the permanent "ideas" board for
feature requests (highest-voted first; visitors up- or downvote each idea once). Replies are
one level deep and belong to their parent's board.

Posting needs a bot check, all done on this server with no third-party service:
  * proof of work: the browser fetches a signed challenge and must find a number whose
    SHA-256 (with the challenge) starts with DISCUSSION_POW_BITS zero bits, about a second of
    work for one post but expensive for a bot sending thousands; each challenge works once
  * a hidden "website" field that people never see but form-filling bots fill in
  * a minimum time between getting the challenge and posting, and per-visitor post limits

Posts must pass api/moderation.py: plain text only, no links or images, no swear words or
sexual language. Reactions (one of each kind per visitor, toggled) use an anonymous random
cookie, so no account is needed.

Writes go straight to Postgres through the API's database user, which may only insert into the
discussion tables (scripts/setup-web-db-user.sh); everything else stays read-only.
"""

import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from api.live import jsonable
from api.moderation import MAX_AUTHOR, MAX_BODY, Rejected, clean_text
from api.security import client_ip, require_admin

router = APIRouter(prefix="/api/discussion")

REACTIONS = ("like", "love", "haha", "wow", "sad")
POW_BITS = int(os.getenv("DISCUSSION_POW_BITS", "18"))  # ~260k hashes on average
# Challenges are signed with this key. Without DISCUSSION_SECRET a new key is made at startup,
# which only means challenges handed out before a restart stop working.
SECRET = os.getenv("DISCUSSION_SECRET", "").encode() or secrets.token_bytes(32)
CHALLENGE_TTL = 15 * 60
MIN_SECONDS_TO_POST = 2
POST_GAP_SECONDS = 30
POSTS_PER_HOUR = 10
PAGE_SIZE = 50

VISITOR_COOKIE = "bt_visitor"
VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")


# --- bot check ------------------------------------------------------------------------

def _sign(payload: str) -> str:
    return hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte:
            return bits + 8 - byte.bit_length()
        bits += 8
    return bits


class BotCheck:
    def __init__(self):
        self._used: dict[str, float] = {}  # challenge -> expiry, so each one works once

    def issue(self) -> dict:
        payload = f"{int(time.time())}.{secrets.token_hex(12)}"
        return {"challenge": f"{payload}.{_sign(payload)}", "bits": POW_BITS}

    def verify(self, challenge: str, solution: int) -> None:
        try:
            issued, nonce, signature = challenge.split(".")
            age = time.time() - int(issued)
        except ValueError:
            raise Rejected("The bot check failed. Verify again and repost.")
        if not hmac.compare_digest(signature, _sign(f"{issued}.{nonce}")):
            raise Rejected("The bot check failed. Verify again and repost.")
        if age > CHALLENGE_TTL:
            raise Rejected("The bot check expired. Verify again and repost.")
        if age < MIN_SECONDS_TO_POST:
            raise Rejected("That was very fast. Wait a moment, then post again.")
        digest = hashlib.sha256(f"{challenge}:{solution}".encode()).digest()
        if _leading_zero_bits(digest) < POW_BITS:
            raise Rejected("The bot check failed. Verify again and repost.")

        now = time.time()
        for c in [c for c, expiry in self._used.items() if expiry < now]:
            del self._used[c]
        if challenge in self._used:
            raise Rejected("That bot check was already used. Verify again to post again.")
        self._used[challenge] = now + CHALLENGE_TTL


class PostLimiter:
    """At most one post every POST_GAP_SECONDS and POSTS_PER_HOUR per visitor."""

    def __init__(self):
        self._posts: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.time()
        times = self._posts[key]
        while times and now - times[0] > 3600:
            times.popleft()
        if times and now - times[-1] < POST_GAP_SECONDS:
            raise Rejected(f"You can post once every {POST_GAP_SECONDS} seconds. Try again shortly.")
        if len(times) >= POSTS_PER_HOUR:
            raise Rejected(f"You've reached {POSTS_PER_HOUR} posts this hour. Try again later.")

    def record(self, key: str) -> None:
        self._posts[key].append(time.time())


bot_check = BotCheck()
post_limiter = PostLimiter()


# --- helpers ----------------------------------------------------------------------------

def _visitor(request: Request, response: Response) -> str:
    """The anonymous visitor id from the cookie, created on first use."""
    visitor = request.cookies.get(VISITOR_COOKIE, "")
    if not VISITOR_RE.match(visitor):
        visitor = secrets.token_urlsafe(18)
        response.set_cookie(VISITOR_COOKIE, visitor, max_age=365 * 24 * 3600, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return visitor


def _db(request: Request):
    return request.app.state.db.connection()


BOARDS = ("general", "ideas")

POST_COLUMNS = """
    p.id, p.board, p.parent_id, p.author, p.body, p.created_at,
    COALESCE((SELECT json_object_agg(kind, n) FROM (
        SELECT kind, count(*) AS n FROM discussion_reactions r WHERE r.post_id = p.id GROUP BY kind) k),
        '{}'::json) AS reactions,
    COALESCE((SELECT array_agg(kind) FROM discussion_reactions r
              WHERE r.post_id = p.id AND r.visitor = %(visitor)s), '{}') AS mine,
    COALESCE((SELECT sum(value) FROM discussion_votes v WHERE v.post_id = p.id), 0)::int AS score,
    COALESCE((SELECT value FROM discussion_votes v WHERE v.post_id = p.id AND v.visitor = %(visitor)s), 0)::int
        AS my_vote
"""
# General discussion: oldest first. Feature ideas: highest score first, then oldest.
ORDER = {"general": "p.created_at, p.id", "ideas": "score DESC, p.created_at, p.id"}


def _not_set_up() -> HTTPException:
    return HTTPException(503, "The discussion isn't set up yet (run scripts/setup-web-db-user.sh).")


def _one(conn, post_id: int, visitor: str) -> dict:
    """One post as the API returns it (a new top-level post has no replies yet)."""
    row = conn.execute(f"SELECT {POST_COLUMNS} FROM discussion_posts p WHERE p.id = %(id)s",
                       {"id": post_id, "visitor": visitor}).fetchone()
    return {**jsonable(row), **({"replies": []} if row["parent_id"] is None else {})}


# --- endpoints --------------------------------------------------------------------------

@router.get("")
def list_posts(request: Request, response: Response, board: str = Query("general"),
               offset: int = Query(0, ge=0), limit: int = Query(PAGE_SIZE, ge=1, le=100)) -> dict:
    """A page of a board's posts, each with all its replies (oldest first)."""
    if board not in BOARDS:
        raise HTTPException(422, f"board must be one of: {', '.join(BOARDS)}")
    params = {"visitor": _visitor(request, response), "board": board, "offset": offset, "limit": limit + 1}
    try:
        with _db(request) as conn:
            rows = conn.execute(
                f"SELECT {POST_COLUMNS} FROM discussion_posts p "
                "WHERE p.board = %(board)s AND p.parent_id IS NULL AND NOT p.hidden "
                f"ORDER BY {ORDER[board]} OFFSET %(offset)s LIMIT %(limit)s", params).fetchall()[:limit]
            replies = conn.execute(
                f"SELECT {POST_COLUMNS} FROM discussion_posts p "
                "WHERE p.parent_id = ANY(%(ids)s) AND NOT p.hidden ORDER BY p.created_at, p.id",
                {**params, "ids": [r["id"] for r in rows]}).fetchall()
            total = conn.execute("SELECT count(*) AS n FROM discussion_posts WHERE board = %(board)s "
                                 "AND parent_id IS NULL AND NOT hidden", params).fetchone()["n"]
    except psycopg.errors.UndefinedTable:
        raise _not_set_up()
    by_parent: dict[int, list[dict]] = {}
    for reply in replies:
        by_parent.setdefault(reply["parent_id"], []).append(jsonable(reply))
    posts = [{**jsonable(r), "replies": by_parent.get(r["id"], [])} for r in rows]
    return {"posts": posts, "has_more": offset + len(posts) < total, "total": total}


@router.get("/challenge")
def challenge(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return bot_check.issue()


class NewPost(BaseModel):
    board: str = "general"
    parent_id: int | None = None  # set to reply to a post
    author: str = ""
    body: str = ""
    website: str = ""  # honeypot: hidden from people, so it must stay empty
    challenge: str = ""
    solution: int = -1


@router.post("", status_code=201)
def create_post(post: NewPost, request: Request, response: Response) -> dict:
    if post.board not in BOARDS:
        raise HTTPException(422, f"board must be one of: {', '.join(BOARDS)}")
    visitor = _visitor(request, response)
    limit_key = client_ip(request)
    what = "reply" if post.parent_id else "idea" if post.board == "ideas" else "post"
    try:
        if post.website:
            raise Rejected("The bot check failed. Verify again and repost.")
        # Content first, so people learn what to fix before being told to wait.
        author = clean_text(post.author, "name", MAX_AUTHOR, required=False) or "Anonymous"
        body = clean_text(post.body, what, MAX_BODY, required=True)
        post_limiter.check(limit_key)
        bot_check.verify(post.challenge, post.solution)
    except Rejected as exc:
        raise HTTPException(400, str(exc))

    try:
        with _db(request) as conn:
            conn.execute("SET TRANSACTION READ WRITE")  # the web role is read-only by default
            board = post.board
            if post.parent_id is not None:
                # Replies are one level deep and live on their parent's board.
                parent = conn.execute("SELECT board, parent_id FROM discussion_posts WHERE id = %s AND NOT hidden",
                                      (post.parent_id,)).fetchone()
                if not parent:
                    raise HTTPException(404, "The post you're replying to no longer exists")
                if parent["parent_id"] is not None:
                    raise HTTPException(400, "Reply to the original post, not to a reply")
                board = parent["board"]
            new_id = conn.execute(
                "INSERT INTO discussion_posts (board, parent_id, author, body) VALUES (%s, %s, %s, %s) RETURNING id",
                (board, post.parent_id, author, body)).fetchone()["id"]
            created = _one(conn, new_id, visitor)
    except psycopg.errors.UndefinedTable:
        raise _not_set_up()
    post_limiter.record(limit_key)
    return created


class Vote(BaseModel):
    value: int  # 1 = upvote, -1 = downvote, 0 = take the vote back


@router.post("/{post_id}/vote")
def vote(post_id: int, body: Vote, request: Request, response: Response) -> dict:
    """Up- or downvote a feature idea; one vote per visitor, which they can change or take back."""
    if body.value not in (-1, 0, 1):
        raise HTTPException(400, "value must be 1, -1 or 0")
    visitor = _visitor(request, response)
    params = {"id": post_id, "visitor": visitor, "value": body.value}
    with _db(request) as conn:
        conn.execute("SET TRANSACTION READ WRITE")
        if not conn.execute("SELECT 1 FROM discussion_posts WHERE id = %(id)s AND board = 'ideas' "
                            "AND parent_id IS NULL AND NOT hidden", params).fetchone():
            raise HTTPException(404, "Only feature ideas can be voted on")
        if body.value:
            conn.execute("INSERT INTO discussion_votes (post_id, visitor, value) VALUES (%(id)s, %(visitor)s, "
                         "%(value)s) ON CONFLICT (post_id, visitor) DO UPDATE SET value = EXCLUDED.value", params)
        else:
            conn.execute("DELETE FROM discussion_votes WHERE post_id = %(id)s AND visitor = %(visitor)s", params)
        row = _one(conn, post_id, visitor)
    return {"score": row["score"], "my_vote": row["my_vote"]}


class Reaction(BaseModel):
    kind: str


@router.post("/{post_id}/reactions")
def toggle_reaction(post_id: int, reaction: Reaction, request: Request, response: Response) -> dict:
    """Add the visitor's reaction, or remove it if they already gave that one."""
    if reaction.kind not in REACTIONS:
        raise HTTPException(400, f"Reaction must be one of: {', '.join(REACTIONS)}")
    visitor = _visitor(request, response)
    params = {"id": post_id, "visitor": visitor, "kind": reaction.kind}
    with _db(request) as conn:
        conn.execute("SET TRANSACTION READ WRITE")
        if not conn.execute("SELECT 1 FROM discussion_posts WHERE id = %(id)s AND NOT hidden", params).fetchone():
            raise HTTPException(404, "That post no longer exists")
        removed = conn.execute("DELETE FROM discussion_reactions WHERE post_id = %(id)s AND visitor = %(visitor)s "
                               "AND kind = %(kind)s", params).rowcount
        if not removed:
            conn.execute("INSERT INTO discussion_reactions (post_id, visitor, kind) VALUES (%(id)s, %(visitor)s, "
                         "%(kind)s) ON CONFLICT DO NOTHING", params)
        row = conn.execute(f"SELECT {POST_COLUMNS} FROM discussion_posts p WHERE p.id = %(id)s", params).fetchone()
    return {"reactions": row["reactions"], "mine": row["mine"]}


@router.delete("/{post_id}", status_code=204, dependencies=[Depends(require_admin)])
def hide_post(post_id: int, request: Request) -> None:
    """Moderation: hide a post (admin only: ADMIN_TOKEN, or local admin mode)."""
    with _db(request) as conn:
        conn.execute("SET TRANSACTION READ WRITE")
        conn.execute("UPDATE discussion_posts SET hidden = true WHERE id = %s", (post_id,))
