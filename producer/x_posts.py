"""Every midnight, publish each owner's most popular X post of the day that just ended to x.posts.

How the pick works (per X account):
  * the day is split into X_BLOCK_HOURS blocks in local time (default 24: one block per day,
    picked just after midnight); once a block has ended, read all of its original posts and
    quotes (no replies, no reposts) from the X API v2 user timeline
  * rank them by engagement = likes + reposts + replies, and publish only the top one
  * the Kafka record's timestamp is the post's creation time, so the Streams app can look up
    where the aircraft was at that moment (versioned-table join)

Each post is read once, right after its block ends, so engagement is measured when the post
is at most a day old. If the producer wasn't running at midnight, the missed day is picked when
it starts.

X API pricing is pay-per-use, about $0.005 per post read. A daily spend cap (X_BUDGET_USD,
default $1.00 per day, resetting at local midnight) is tracked in .state/x_posts.json; requests
never ask for more posts than the rest of today's budget covers, and polling stops at the cap
until midnight. This is the app's own estimate. X also enforces the spend cap set in the X
developer console, which must allow at least X_BUDGET_USD a day (about 30x that per month).

When the cap is reached, or X refuses requests (credits used up, the monthly spend cap set in
the X developer console reached, or a bad token), only X polling pauses: positions and weather keep updating, and every post already stored stays on
the site, so each owner's latest post keeps showing. The pause is recorded in the state file
(the API reads it to tell visitors). A refused request is retried only once a day, also across
producer restarts, so polling resumes by itself after you top up or the monthly cap resets; a
budget pause lifts by itself at midnight, when the daily budget resets.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from producer.config import STATE_DIR, TOPIC_X_POSTS

log = logging.getLogger("x_posts")

X_API = "https://api.x.com/2"
STATE_FILE = STATE_DIR / "x_posts.json"

COST_PER_POST = 0.005  # USD, "Posts: Read", charged per post returned
COST_PER_USER_LOOKUP = 0.010  # USD, "User: Read"
X_MIN_RESULTS = 5  # the API rejects max_results below 5
MAX_PAGES = 5  # safety cap per block (100 posts per page)
TIMEOUT = 15
REFUSED_RETRY_SECONDS = 24 * 3600


class RateLimited(Exception):
    def __init__(self, reset_epoch: float):
        super().__init__(f"rate limited until {time.strftime('%H:%M:%S', time.localtime(reset_epoch))}")
        self.reset_epoch = reset_epoch


class XRefused(Exception):
    """X refuses requests until something changes on the account: credits used up, the monthly
    spend cap reached, or an invalid token. Retrying sooner than that only fills the log."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class XClient:
    def __init__(self, bearer_token: str):
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {bearer_token}"

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{X_API}{path}", params=params, timeout=TIMEOUT)
        if resp.status_code == 402 or "spend-cap-reached" in resp.text or "UsageCapExceeded" in resp.text:
            raise XRefused("credits", resp.text[:200])
        if resp.status_code in (401, 403):
            raise XRefused("auth", f"HTTP {resp.status_code}, check x_bearer_token: {resp.text[:200]}")
        if resp.status_code == 429:
            raise RateLimited(float(resp.headers.get("x-rate-limit-reset", time.time() + 900)))
        resp.raise_for_status()
        return resp.json()

    def user_id(self, username: str) -> str:
        body = self._get(f"/users/by/username/{username}")
        if "data" not in body:
            raise RuntimeError(f"X user @{username} not found: {body.get('errors')}")
        return body["data"]["id"]

    def posts_page(self, user_id: str, start: datetime | None, end: datetime | None, max_results: int,
                   page_token: str | None) -> tuple[list[dict], str | None]:
        """One page (newest first) of original posts/quotes, optionally between start and end,
        and the next-page token."""
        params = {
            "exclude": "retweets,replies",
            "tweet.fields": "created_at,public_metrics,referenced_tweets,note_tweet",
            "max_results": max_results,
        }
        if start:
            params["start_time"] = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if end:
            params["end_time"] = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if page_token:
            params["pagination_token"] = page_token
        body = self._get(f"/users/{user_id}/tweets", params)
        return body.get("data", []), body.get("meta", {}).get("next_token")


def engagement(post: dict) -> int:
    return sum(post.get(k) or 0 for k in ("like_count", "repost_count", "reply_count"))


def _to_post(tweet: dict, username: str) -> dict:
    """An API tweet object shaped like the XPost schema minus `hex`."""
    refs = {r["type"] for r in tweet.get("referenced_tweets", [])}
    post_type = "reply" if "replied_to" in refs else "quote" if "quoted" in refs else "post"
    metrics = tweet.get("public_metrics", {})
    created = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
    return {
        "post_id": tweet["id"],
        "username": username,
        # Long posts are truncated in `text`; the full version is in note_tweet.
        "text": (tweet.get("note_tweet") or {}).get("text") or tweet.get("text", ""),
        "post_type": post_type,
        "url": f"https://x.com/{username}/status/{tweet['id']}",
        "like_count": metrics.get("like_count"),
        "reply_count": metrics.get("reply_count"),
        "repost_count": metrics.get("retweet_count"),
        "view_count": metrics.get("impression_count"),
        "created_at": int(created.timestamp() * 1000),
    }


class XPostPoller:
    """Publishes each tracked owner's top post of the last completed block, keyed by aircraft hex."""

    def __init__(self, bearer_token: str, publisher):
        self._client = XClient(bearer_token)
        self._publisher = publisher
        self._budget = float(os.getenv("X_BUDGET_USD", "1.00"))
        self._block_hours = int(os.getenv("X_BLOCK_HOURS", "24"))
        if 24 % self._block_hours:
            raise RuntimeError("X_BLOCK_HOURS must divide 24 (e.g. 1, 2, 3, 4, 6, 8, 12, 24)")
        self._paused_until = 0.0
        self._state = self._load_state()
        paused = self._state["paused"]
        if paused and paused["reason"] == "budget" and self._affordable_posts() >= X_MIN_RESULTS:
            self._resume()  # a new day, or the budget was raised
        elif paused and paused["reason"] != "budget":
            # X refused before this restart: wait out the rest of the day instead of asking again now.
            self._paused_until = datetime.fromisoformat(paused["since"]).timestamp() + REFUSED_RETRY_SECONDS
        log.info("X: top post per %dh block; spent today $%.3f of $%.2f a day",
                 self._block_hours, self._spent_today(), self._budget)

    def _spent_today(self) -> float:
        """Estimated spend since local midnight; the daily budget starts over each day."""
        today = datetime.now().astimezone().date().isoformat()
        if self._state["spend_day"] != today:
            self._state["spend_day"], self._state["spent_today"] = today, 0.0
            if (self._state["paused"] or {}).get("reason") == "budget":
                self._resume()
        return self._state["spent_today"]

    @property
    def _remaining(self) -> float:
        return self._budget - self._spent_today()

    def _affordable_posts(self) -> int:
        return int(round(self._remaining / COST_PER_POST, 6))

    def _last_completed_block(self) -> tuple[datetime, datetime]:
        """(start, end) of the most recent block that has ended, in local time."""
        now = datetime.now().astimezone()
        current_start = now.replace(hour=now.hour - now.hour % self._block_hours, minute=0, second=0, microsecond=0)
        return current_start - timedelta(hours=self._block_hours), current_start

    def poll(self, tracked: dict[str, dict]) -> None:
        start, end = self._last_completed_block()
        if time.time() < self._paused_until:
            return
        block = start.isoformat()

        hexes_by_user: dict[str, list[str]] = {}
        for hex_code, info in tracked.items():
            if username := info.get("x_username"):
                hexes_by_user.setdefault(username, []).append(hex_code)

        for username, hexes in hexes_by_user.items():
            if self._state["picked_blocks"].get(username) == block:
                continue  # this block is already done
            if self._affordable_posts() < X_MIN_RESULTS:
                self._pause("budget")
                return
            try:
                top = self._top_post(username, start, end)
            except RateLimited as exc:
                self._paused_until = exc.reset_epoch
                log.warning("X API %s", exc)
                return
            except XRefused as exc:
                self._pause(exc.reason, str(exc))
                self._paused_until = time.time() + REFUSED_RETRY_SECONDS
                return
            self._resume()

            if top:
                # Record timestamp = post time, so Streams joins the aircraft's position *at that time*.
                for hex_code in hexes:
                    self._publisher.publish(TOPIC_X_POSTS, hex_code, {**top, "hex": hex_code},
                                            timestamp_ms=top["created_at"])
                self._publisher.flush()
                log.info("@%s top post of %s–%s: %s (engagement %d) — X spend today $%.3f of $%.2f",
                         username, start.strftime("%b %d %H:%M"), end.strftime("%H:%M"), top["post_id"],
                         engagement(top), self._spent_today(), self._budget)
            else:
                log.info("@%s: no original posts %s–%s", username, start.strftime("%b %d %H:%M"), end.strftime("%H:%M"))
            self._state["picked_blocks"][username] = block
            self._state["checked_at"] = datetime.now(timezone.utc).isoformat()  # shown in the site's header
            self._save_state()

        # When the next pick is due (the end of the block in progress), also for the header.
        next_check = (end + timedelta(hours=self._block_hours)).isoformat()
        if self._state.get("next_check_at") != next_check:
            self._state["next_check_at"] = next_check
            self._save_state()

    def backfill_latest(self, aircraft: dict[str, dict]) -> None:
        """One-off: publish each owner's most recent original post (whatever its age), so the site
        isn't empty before the first daily pick. Reads X's minimum page of 5 posts per account."""
        hexes_by_user: dict[str, list[str]] = {}
        for hex_code, info in aircraft.items():
            if username := info.get("x_username"):
                hexes_by_user.setdefault(username, []).append(hex_code)

        for username, hexes in hexes_by_user.items():
            if self._affordable_posts() < X_MIN_RESULTS:
                log.warning("Daily X budget of $%.2f reached: skipping @%s", self._budget, username)
                continue
            try:
                page, _ = self._client.posts_page(self._user_id(username), None, None, X_MIN_RESULTS, None)
            except XRefused as exc:
                self._pause(exc.reason, str(exc))
                return
            self._charge(len(page) * COST_PER_POST)
            if not page:
                log.info("@%s: no original posts found", username)
                continue
            latest = max((_to_post(t, username) for t in page), key=lambda p: int(p["post_id"]))
            for hex_code in hexes:
                self._publisher.publish(TOPIC_X_POSTS, hex_code, {**latest, "hex": hex_code},
                                        timestamp_ms=latest["created_at"])
            self._publisher.flush()
            posted = datetime.fromtimestamp(latest["created_at"] / 1000).astimezone()
            log.info("@%s latest post %s from %s — X spend today $%.3f of $%.2f", username, latest["post_id"],
                     posted.strftime("%b %d %H:%M"), self._spent_today(), self._budget)

    def _top_post(self, username: str, start: datetime, end: datetime) -> dict | None:
        """Read every original post/quote between start and end and return the most engaging."""
        user_id = self._user_id(username)

        tweets: list[dict] = []
        page_token = None
        for _ in range(MAX_PAGES):
            wanted = min(100, self._affordable_posts())  # never request more than the budget covers
            if wanted < X_MIN_RESULTS:
                log.warning("X budget ran out while reading %s; picking from %d posts read", start, len(tweets))
                break
            page, page_token = self._client.posts_page(user_id, start, end, wanted, page_token)
            self._charge(len(page) * COST_PER_POST)
            tweets.extend(page)
            if not page_token:
                break

        posts = [_to_post(t, username) for t in tweets]
        return max(posts, key=lambda p: (engagement(p), p.get("view_count") or 0), default=None)

    def _pause(self, reason: str, detail: str = "") -> None:
        """Stop X polling (the rest of the app keeps running) and record why."""
        if reason == "budget":
            if (self._state["paused"] or {}).get("reason") == "budget":
                return
            log.warning("Daily X budget of $%.2f reached ($%.3f spent today): X polling paused until "
                        "midnight, the latest stored posts stay on the site.", self._budget, self._spent_today())
        else:
            log.warning("X API refused the request (%s). X polling paused, the latest stored posts stay on the "
                        "site; trying again in 24 hours.", detail)
        # "since" is the last refusal, so the 24-hour wait also holds across producer restarts.
        self._state["paused"] = {"reason": reason, "since": datetime.now(timezone.utc).isoformat()}
        self._save_state()

    def _resume(self) -> None:
        if self._state["paused"]:
            log.info("X polling resumed")
            self._state["paused"] = None
            self._save_state()

    def _user_id(self, username: str) -> str:
        ids = self._state["user_ids"]
        if username not in ids:  # a user lookup is billed too, so it's cached
            ids[username] = self._client.user_id(username)
            self._charge(COST_PER_USER_LOOKUP)
        return ids[username]

    def _charge(self, usd: float) -> None:
        self._state["spent_today"] = round(self._spent_today() + usd, 6)
        self._state["spent_usd"] = round(self._state["spent_usd"] + usd, 6)  # all-time total
        self._save_state()

    @staticmethod
    def _load_state() -> dict:
        try:
            state = json.loads(STATE_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
        state.setdefault("user_ids", {})
        state.setdefault("picked_blocks", {})
        state.setdefault("spent_usd", 0.0)  # all-time estimate
        state.setdefault("spend_day", None)  # local date that spent_today belongs to
        state.setdefault("spent_today", 0.0)
        state.setdefault("checked_at", None)  # last successful pick, ISO time
        state.setdefault("paused", None)  # {"reason": "budget" | "credits" | "auth", "since": iso time} or None
        return state

    def _save_state(self) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(self._state, indent=2))
