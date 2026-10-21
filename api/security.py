"""Protections for serving the API to the public internet (through a Cloudflare Tunnel).

* READ_ONLY (default on): visitors can't add/remove aircraft. You can still make changes from
  your own terminal by sending `Authorization: Bearer <ADMIN_TOKEN>` (if ADMIN_TOKEN is set).
* A per-IP rate limit for HTTP requests, and caps on concurrent WebSocket connections.
* Standard security headers.

Behind Cloudflare, every request arrives from cloudflared on localhost; the visitor's real IP
is in the CF-Connecting-IP header.
"""

import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

READ_ONLY = os.getenv("READ_ONLY", "true").lower() != "false"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN") or None

REQUESTS_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
MAX_WEBSOCKETS_TOTAL = int(os.getenv("MAX_WEBSOCKETS_TOTAL", "200"))
MAX_WEBSOCKETS_PER_IP = int(os.getenv("MAX_WEBSOCKETS_PER_IP", "4"))

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


def client_ip(conn: HTTPConnection) -> str:
    return conn.headers.get("cf-connecting-ip") or (conn.client.host if conn.client else "unknown")


def require_admin(request: Request) -> None:
    """FastAPI dependency for endpoints that change data."""
    if not READ_ONLY:
        return
    auth = request.headers.get("authorization", "")
    if ADMIN_TOKEN and hmac.compare_digest(auth, f"Bearer {ADMIN_TOKEN}"):
        return
    raise HTTPException(403, "This site is read-only")


class RateLimiter:
    """Sliding one-minute window per key. In-memory, which is fine for a single API process."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_sweep = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        if now - self._last_sweep > 300:  # forget idle visitors so memory stays bounded
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                del self._hits[k]
            self._last_sweep = now
        return True


class WebSocketLimiter:
    def __init__(self, total: int, per_ip: int):
        self.total, self.per_ip = total, per_ip
        self._open: dict[str, int] = defaultdict(int)

    def acquire(self, ip: str) -> bool:
        if sum(self._open.values()) >= self.total or self._open[ip] >= self.per_ip:
            return False
        self._open[ip] += 1
        return True

    def release(self, ip: str) -> None:
        self._open[ip] -= 1
        if self._open[ip] <= 0:
            del self._open[ip]
