"""BillionTracker web API.

    uvicorn api.main:app                      # http://localhost:8000, read-only (the default)
    READ_ONLY=false uvicorn api.main:app      # local admin mode: add/remove aircraft, /docs

REST data comes from Postgres (written by the Kafka Connect JDBC Sink) through a read-only
database user; live updates come straight from Kafka over a WebSocket. Adding/removing aircraft
publishes to the compacted tracked.aircraft topic, which the producer follows. See api/security.py
for what read-only mode and the public protections do.
"""

import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager

import psycopg
from confluent_kafka.schema_registry import SchemaRegistryClient
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from api.discussion import router as discussion_router
from api.history import router as history_router
from api.live import LiveFeed, jsonable
from api.posts import PLACED_POSTS
from api.security import (
    MAX_WEBSOCKETS_PER_IP,
    MAX_WEBSOCKETS_TOTAL,
    READ_ONLY,
    REQUESTS_PER_MINUTE,
    SECURITY_HEADERS,
    RateLimiter,
    WebSocketLimiter,
    client_ip,
    require_admin,
)
from producer.config import (
    PROJECT_ROOT,
    SCHEMA_REGISTRY_URL,
    STATE_DIR,
    TOPIC_TRACKED,
    KAFKA_BOOTSTRAP,
    web_postgres_conninfo,
)
from producer.producer import AvroPublisher
from producer.topics import ensure_topics

WEB_DIST = PROJECT_ROOT / "web" / "dist"
HEX_RE = re.compile(r"^[0-9a-f]{6}$")  # ICAO 24-bit address

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%H:%M:%S")
for name in ("httpx", "httpx2"):
    logging.getLogger(name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_topics(KAFKA_BOOTSTRAP)
    registry = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})
    app.state.publisher = AvroPublisher(registry)
    app.state.feed = LiveFeed(registry)
    app.state.feed.start(asyncio.get_running_loop())
    # A small pool instead of a connection per request; the DB role also caps connections.
    app.state.db = ConnectionPool(web_postgres_conninfo(), min_size=1, max_size=8,
                                  kwargs={"row_factory": dict_row}, open=True)
    logging.getLogger("api").info("read-only mode: %s", READ_ONLY)
    yield
    app.state.db.close()
    app.state.feed.stop()
    app.state.publisher.flush()


# The interactive API docs are only exposed in local admin mode.
app = FastAPI(
    title="BillionTracker",
    lifespan=lifespan,
    docs_url=None if READ_ONLY else "/docs",
    redoc_url=None,
    openapi_url=None if READ_ONLY else "/openapi.json",
)

app.include_router(history_router)
app.include_router(discussion_router)

rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)
ws_limiter = WebSocketLimiter(MAX_WEBSOCKETS_TOTAL, MAX_WEBSOCKETS_PER_IP)


@app.middleware("http")
async def protect(request: Request, call_next):
    if not rate_limiter.allow(client_ip(request)):
        return JSONResponse({"detail": "Too many requests, slow down"}, status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


# --- helpers -------------------------------------------------------------------------

def valid_hex(hex_code: str) -> str:
    hex_code = hex_code.strip().lower()
    if not HEX_RE.match(hex_code):
        raise HTTPException(422, "hex must be a 6-character ICAO address, e.g. a835af")
    return hex_code


def query(sql: str, params: tuple) -> list[dict]:
    with app.state.db.connection() as conn:
        try:
            return conn.execute(sql, params).fetchall()
        except psycopg.errors.UndefinedTable:
            return []  # the JDBC Sink creates the table on its first write


def aircraft_info(hex_code: str, info: dict) -> dict:
    return {
        "hex": hex_code,
        "owner": info.get("owner"),
        "registration": info.get("registration"),
        "aircraft_type": info.get("aircraft_type"),
        "x_username": info.get("x_username"),
        "icon_url": f"/api/aircraft/{hex_code}/icon" if info.get("icon") else None,
        "icon_credit": info.get("icon_credit"),
        "icon_source_url": info.get("icon_source_url"),
    }


@app.get("/api/config")
def frontend_config() -> dict:
    """Settings the browser needs. A Maps JavaScript key is always visible to the browser;
    protect it with an HTTP-referrer restriction (your public URL) in Google Cloud."""
    return {
        "google_maps_api_key": os.getenv("google_maps_api_key", ""),
        "read_only": READ_ONLY,
        "x_paused": x_paused(),
    }


@app.get("/api/status")
def update_status() -> dict:
    """When positions/weather and X posts were last updated and are next due (for the header).
    The producer records these in .state; before its first run, the newest stored position is
    used instead."""
    updates = _read_state("updates.json")
    x_state = _read_state("x_posts.json")
    positions_at = updates.get("positions_updated_at")
    if not positions_at:
        rows = query("SELECT max(ts) AS ts FROM flight_enriched", ())
        positions_at = jsonable(rows[0])["ts"] if rows else None
    paused = x_state.get("paused")
    return {
        "positions_updated_at": positions_at,
        "next_positions_at": updates.get("next_positions_at"),
        "x_updated_at": x_state.get("checked_at"),
        "next_x_at": None if paused else x_state.get("next_check_at"),
        "x_paused": bool(paused),
    }


def _read_state(name: str) -> dict:
    try:
        return json.loads((STATE_DIR / name).read_text())
    except (OSError, ValueError):
        return {}


def x_paused() -> bool:
    """True while the producer has paused X polling (budget cap or credits used up).
    Stored posts are still served; only new ones stop arriving."""
    try:
        return bool(json.loads((STATE_DIR / "x_posts.json").read_text()).get("paused"))
    except (OSError, ValueError):
        return False


# --- tracked aircraft ------------------------------------------------------------------

class NewAircraft(BaseModel):
    hex: str
    owner: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None


@app.get("/api/aircraft")
def list_aircraft() -> list[dict]:
    tracked = dict(app.state.feed.tracked.aircraft)
    return [aircraft_info(h, info) for h, info in sorted(tracked.items())]


@app.post("/api/aircraft", status_code=202, dependencies=[Depends(require_admin)])
def add_aircraft(body: NewAircraft) -> dict:
    """Start tracking an aircraft. The producer picks it up on its next poll cycle."""
    hex_code = valid_hex(body.hex)
    existing = app.state.feed.tracked.aircraft.get(hex_code, {})
    record = {
        "hex": hex_code,
        "owner": body.owner or existing.get("owner"),
        # The photo and its credit are configured in config/billionaires.csv.
        "icon": existing.get("icon"),
        "icon_credit": existing.get("icon_credit"),
        "icon_source_url": existing.get("icon_source_url"),
        "registration": body.registration or existing.get("registration"),
        "aircraft_type": body.aircraft_type or existing.get("aircraft_type"),
        "x_username": existing.get("x_username"),  # also configured in config/billionaires.csv
        "added_at": int(time.time() * 1000),
    }
    app.state.publisher.publish(TOPIC_TRACKED, hex_code, record)
    app.state.publisher.flush()
    return aircraft_info(hex_code, record)


@app.delete("/api/aircraft/{hex_code}", status_code=204, dependencies=[Depends(require_admin)])
def remove_aircraft(hex_code: str) -> None:
    """Stop tracking: a tombstone (null value) on the compacted topic deletes the entry."""
    hex_code = valid_hex(hex_code)
    app.state.publisher.publish(TOPIC_TRACKED, hex_code, None)
    app.state.publisher.flush()


@app.get("/api/aircraft/{hex_code}/icon")
def aircraft_icon(hex_code: str) -> FileResponse:
    info = app.state.feed.tracked.aircraft.get(valid_hex(hex_code)) or {}
    if not info.get("icon"):
        raise HTTPException(404, "no icon configured for this aircraft")
    path = (PROJECT_ROOT / info["icon"]).resolve()
    if not path.is_relative_to(PROJECT_ROOT) or not path.is_file():
        raise HTTPException(404, "icon file not found")
    return FileResponse(path)


# --- flight data (Postgres) --------------------------------------------------------------

@app.get("/api/latest")
def latest_all() -> list[dict]:
    """Latest enriched record for every tracked aircraft (the homepage's fleet map)."""
    hexes = list(app.state.feed.tracked.aircraft)
    if not hexes:
        return []
    rows = query(
        "SELECT DISTINCT ON (hex) * FROM flight_enriched WHERE hex = ANY(%s) ORDER BY hex, ts DESC",
        (hexes,),
    )
    return [jsonable(r) for r in rows]


@app.get("/api/aircraft/{hex_code}/latest")
def latest(hex_code: str) -> dict:
    rows = query("SELECT * FROM flight_enriched WHERE hex = %s ORDER BY ts DESC LIMIT 1", (valid_hex(hex_code),))
    if not rows:
        raise HTTPException(404, "no data for this aircraft yet")
    return jsonable(rows[0])


@app.get("/api/aircraft/{hex_code}/track")
def track(hex_code: str, limit: int = Query(500, ge=1, le=5000)) -> list[dict]:
    """Position history, oldest first."""
    rows = query(
        "SELECT ts, lat, lon, altitude_ft, ground_speed_kt, track_deg FROM flight_enriched "
        "WHERE hex = %s ORDER BY ts DESC LIMIT %s",
        (valid_hex(hex_code), limit),
    )
    return [jsonable(r) for r in reversed(rows)]


@app.get("/api/aircraft/{hex_code}/posts")
def posts(hex_code: str, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    """The owner's X posts with the aircraft's position at the time, newest first."""
    rows = query(
        f"SELECT * FROM {PLACED_POSTS} WHERE hex = %s ORDER BY created_at DESC LIMIT %s",
        (valid_hex(hex_code), limit),
    )
    return [jsonable(r) for r in rows]


# --- live updates (Kafka) ----------------------------------------------------------------

@app.websocket("/ws/{hex_code}")
async def live(websocket: WebSocket, hex_code: str) -> None:
    """Pushes {"type": "flight" | "post", "data": {...}} as records arrive on Kafka.
    /ws/all streams every tracked aircraft; /ws/<hex> streams just one."""
    hex_code = hex_code.strip().lower()
    if hex_code == "all":
        hex_code = LiveFeed.ALL
    elif hex_code not in app.state.feed.tracked.aircraft:
        await websocket.close(code=1008)  # policy violation: not a tracked aircraft
        return
    ip = client_ip(websocket)
    if not ws_limiter.acquire(ip):
        await websocket.close(code=1013)  # try again later: too many live connections
        return
    await websocket.accept()
    queue = app.state.feed.subscribe(hex_code)
    try:
        while True:
            kind, record = await queue.get()
            await websocket.send_json({"type": kind, "data": record})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        app.state.feed.unsubscribe(hex_code, queue)
        ws_limiter.release(ip)


# --- static files -------------------------------------------------------------------------

if WEB_DIST.is_dir():  # the built React app (Step 8); mounted last so /api and /ws win
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
