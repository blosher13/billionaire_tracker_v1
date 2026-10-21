"""Settings loaded from the project's .env file, plus topic names and schema paths."""

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
BILLIONAIRES_CONFIG = PROJECT_ROOT / "config" / "billionaires.csv"
STATE_DIR = PROJECT_ROOT / ".state"  # small local state, e.g. the last X post id seen

load_dotenv(PROJECT_ROOT / ".env")

# Local infrastructure, shared by the producer and the API.
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
# The broker listens on IPv4 only; without this, clients may try localhost's IPv6 address (::1)
# first and log "Connect to ipv6#[::1]:9092 failed" before falling back to IPv4.
KAFKA_CLIENT = {"bootstrap.servers": KAFKA_BOOTSTRAP, "broker.address.family": "v4"}
SCHEMA_REGISTRY_URL = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081")

# Topic names, shared by every component.
TOPIC_POSITIONS = "flight.positions"
TOPIC_WEATHER = "weather.observations"
TOPIC_ENRICHED = "flight.enriched"
TOPIC_TRACKED = "tracked.aircraft"
TOPIC_X_POSTS = "x.posts"
TOPIC_X_POSTS_LOCATED = "x.posts.located"


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name} in .env (see .env.example)")
    return value


def postgres_conninfo() -> str:
    """libpq connection string for the database the JDBC Sink writes into."""
    from psycopg.conninfo import make_conninfo  # quotes values safely (e.g. spaces in passwords)

    return make_conninfo(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "billiontracker"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=_require("post_gres_password"),
    )


def web_postgres_conninfo() -> str:
    """Connection string for the API's read-only Postgres user (scripts/setup-web-db-user.sh)."""
    from psycopg.conninfo import make_conninfo

    return make_conninfo(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "billiontracker"),
        user=os.getenv("POSTGRES_WEB_USER", "billiontracker_web"),
        password=_require("web_db_password"),
    )


def load_schema(filename: str) -> str:
    """Return an .avsc file's contents as a string, the form AvroSerializer expects."""
    return (SCHEMAS_DIR / filename).read_text()


_EMPTY = {"", "na", "n/a", "none", "-"}
_HEX_RE = re.compile(r"^[0-9a-f]{6}$")


def load_aircraft() -> dict[str, dict]:
    """Every aircraft in config/billionaires.csv, keyed by lowercase hex.

    One row per aircraft: name,icon,icon_credit,icon_source_url,x_username,hex,registration,
    aircraft_type. A person with several planes has several rows (repeat their person columns).
    Only `hex` is required. icon_credit/icon_source_url attribute the photo (e.g. Wikimedia
    Commons photos are Creative Commons licensed and must credit the author and license).
    Values are shaped like the TrackedAircraft schema minus `added_at`.
    """
    with BILLIONAIRES_CONFIG.open(newline="", encoding="utf-8-sig") as f:  # -sig: tolerate Excel's BOM
        rows = list(csv.DictReader(f))

    aircraft = {}
    for line_no, row in enumerate(rows, start=2):  # line 1 is the header
        if None in row:  # csv puts extra columns under the key None
            raise RuntimeError(f"{BILLIONAIRES_CONFIG.name} line {line_no}: too many columns "
                               "(quote values that contain commas)")
        # Blank cells, and NA / N/A / none / -, mean "not set".
        row = {k.strip(): None if (v or "").strip().lower() in _EMPTY else v.strip() for k, v in row.items()}
        if not row.get("hex"):
            continue  # blank line
        hex_code = row["hex"].lower()
        if not _HEX_RE.match(hex_code):
            raise RuntimeError(f"{BILLIONAIRES_CONFIG.name} line {line_no}: hex '{row['hex']}' is not a "
                               "6-character ICAO address (a column may be missing or shifted)")
        if hex_code in aircraft:
            raise RuntimeError(f"{BILLIONAIRES_CONFIG.name} line {line_no}: aircraft {hex_code} is listed twice")
        icon = row.get("icon")
        if icon and not (PROJECT_ROOT / icon).is_file():
            raise RuntimeError(f"{BILLIONAIRES_CONFIG.name} line {line_no}: icon not found: {icon}")
        aircraft[hex_code] = {
            "hex": hex_code,
            "owner": row.get("name"),
            "icon": icon,
            "icon_credit": row.get("icon_credit"),
            "icon_source_url": row.get("icon_source_url"),
            "registration": row.get("registration"),
            "aircraft_type": row.get("aircraft_type"),
            "x_username": (row.get("x_username") or "").lstrip("@").lower() or None,
        }
    return aircraft


@dataclass(frozen=True)
class Settings:
    rapidapi_key: str
    openweather_api_key: str
    poll_seconds: int
    x_bearer_token: str | None  # optional: without it, X posts are not polled

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            rapidapi_key=_require("x-rapidapi-key"),
            openweather_api_key=_require("openweather_api_key"),
            poll_seconds=int(os.getenv("POLL_SECONDS", str(3600))),  # default: every hour
            x_bearer_token=os.getenv("x_bearer_token") or os.getenv("twitter_beartoken") or None,
        )
