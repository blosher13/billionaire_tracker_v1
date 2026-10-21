"""Poll the external APIs for every tracked aircraft and publish Avro records to Kafka.

    python -m producer.producer

Flow per cycle, for each aircraft in tracked.aircraft:
    ADS-B position ──► flight.positions        (skipped if the position hasn't changed)
    OpenWeather    ──► weather.observations
then, for owners with an x_username (and x_bearer_token set):
    X API posts    ──► x.posts                 (at midnight: top post of the day that ended)
"""

import json
import logging
import signal
import threading
import time
from datetime import datetime

import requests
from confluent_kafka import OFFSET_BEGINNING, Consumer, Producer, TopicPartition
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer, AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext, StringSerializer

from producer import apis
from producer.config import (
    KAFKA_CLIENT,
    STATE_DIR,
    KAFKA_BOOTSTRAP,
    SCHEMA_REGISTRY_URL,
    TOPIC_POSITIONS,
    TOPIC_TRACKED,
    TOPIC_WEATHER,
    TOPIC_X_POSTS,
    Settings,
    load_aircraft,
    load_schema,
)
from producer.topics import ensure_topics
from producer.x_posts import XPostPoller

log = logging.getLogger("producer")


class AvroPublisher:
    """One Kafka producer that writes Avro values (schema per topic) with string keys."""

    def __init__(self, registry: SchemaRegistryClient):
        self._producer = Producer({
            **KAFKA_CLIENT,
            "enable.idempotence": True,  # no duplicates from internal retries; implies acks=all
            "linger.ms": 50,
        })
        self._key = StringSerializer("utf_8")
        # The first serialize() call per topic registers the schema under subject "<topic>-value".
        self._values = {
            TOPIC_POSITIONS: AvroSerializer(registry, load_schema("position.avsc")),
            TOPIC_WEATHER: AvroSerializer(registry, load_schema("weather.avsc")),
            TOPIC_TRACKED: AvroSerializer(registry, load_schema("tracked_aircraft.avsc")),
            TOPIC_X_POSTS: AvroSerializer(registry, load_schema("x_post.avsc")),
        }

    def publish(self, topic: str, key: str, value: dict | None, timestamp_ms: int | None = None) -> None:
        """Queue one record. value=None sends a tombstone (used on the compacted topic).
        timestamp_ms sets the record's event time (default: now)."""
        serialized = (
            None if value is None
            else self._values[topic](value, SerializationContext(topic, MessageField.VALUE))
        )
        self._producer.produce(
            topic,
            key=self._key(key, SerializationContext(topic, MessageField.KEY)),
            value=serialized,
            on_delivery=self._on_delivery,
            **({"timestamp": timestamp_ms} if timestamp_ms is not None else {}),
        )
        self._producer.poll(0)  # serve delivery callbacks from earlier sends

    def flush(self) -> None:
        self._producer.flush(10)

    @staticmethod
    def _on_delivery(err, msg) -> None:
        if err:
            log.error("delivery to %s failed: %s", msg.topic(), err)
        else:
            log.debug("delivered %s [%d] @ %d", msg.topic(), msg.partition(), msg.offset())


class TrackedAircraft:
    """The set of aircraft to poll, kept in sync with the compacted tracked.aircraft topic.

    Reads the topic from the beginning every time the producer starts, so it rebuilds the
    full set (compaction keeps the latest record per hex), then picks up adds/removes
    (tombstones) published later — e.g. by the API when you add an aircraft in the UI.
    """

    def __init__(self, registry: SchemaRegistryClient, group_id: str = "billiontracker-producer"):
        self._consumer = Consumer({
            **KAFKA_CLIENT,
            "group.id": group_id,
            "enable.auto.commit": False,  # we always re-read from the start; no offsets to keep
        })
        partitions = self._consumer.list_topics(TOPIC_TRACKED, timeout=10).topics[TOPIC_TRACKED].partitions
        self._consumer.assign([TopicPartition(TOPIC_TRACKED, p, OFFSET_BEGINNING) for p in partitions])
        self._deserialize = AvroDeserializer(registry)
        self.aircraft: dict[str, dict] = {}

    def refresh(self, timeout: float = 0.2) -> None:
        """Apply every record currently available on the topic."""
        while (msg := self._consumer.poll(timeout)) is not None:
            if msg.error():
                log.warning("tracked.aircraft: %s", msg.error())
                continue
            hex_code = msg.key().decode()
            if msg.value() is None:
                if self.aircraft.pop(hex_code, None):
                    log.info("stopped tracking %s", hex_code)
                continue
            info = self._deserialize(msg.value(), SerializationContext(msg.topic(), MessageField.VALUE))
            if hex_code not in self.aircraft:
                log.info("tracking %s (%s)", hex_code, info.get("owner") or "unknown owner")
            self.aircraft[hex_code] = info

    def close(self) -> None:
        self._consumer.close()


class Poller:
    def __init__(self, settings: Settings, publisher: AvroPublisher):
        self.settings = settings
        self.publisher = publisher
        self._last_position: dict[str, dict] = {}  # hex -> last published position

    def poll(self, hex_code: str) -> None:
        position = apis.get_position(self.settings, hex_code)
        if position is None:
            log.info("%s: no position reported", hex_code)
            return

        last = self._last_position.get(hex_code)
        if last and last["ts"] == position["ts"]:
            log.info("%s: position unchanged since last cycle, skipping", hex_code)
            return
        lat, lon = position["lat"], position["lon"]

        # Publish weather BEFORE the position: the Streams app joins each position with the
        # latest weather it has already seen, so it must arrive first.
        weather = apis.get_weather(self.settings, hex_code, lat, lon)
        self.publisher.publish(TOPIC_WEATHER, hex_code, weather)
        log.info("%s: weather %.1f°C %s", hex_code, weather["temp_c"], weather["description"])

        self.publisher.publish(TOPIC_POSITIONS, hex_code, position)
        self._last_position[hex_code] = position
        log.info("%s: position %.4f,%.4f alt=%s ft", hex_code, lat, lon, position["altitude_ft"])


def seed_from_config(publisher: AvroPublisher) -> None:
    """Publish config/billionaires.csv to the compacted topic (re-publishing is harmless)."""
    now_ms = int(time.time() * 1000)
    for hex_code, info in load_aircraft().items():
        publisher.publish(TOPIC_TRACKED, hex_code, {**info, "added_at": now_ms})
    publisher.flush()


TICK_SECONDS = 60
UPDATES_FILE = STATE_DIR / "updates.json"


def save_update_times(next_positions: float) -> None:
    """Record when positions and weather were last fetched and when they're next due; the API
    shows these in the site's header."""
    STATE_DIR.mkdir(exist_ok=True)
    UPDATES_FILE.write_text(json.dumps({
        "positions_updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "next_positions_at": datetime.fromtimestamp(next_positions).astimezone().isoformat(timespec="seconds"),
    }, indent=2))


def _duration(seconds: int) -> str:
    return f"{seconds // 3600}h" if seconds % 3600 == 0 else f"{seconds}s"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for name in ("httpx", "httpx2"):  # Schema Registry client logs every request at INFO
        logging.getLogger(name).setLevel(logging.WARNING)
    settings = Settings.from_env()

    ensure_topics(KAFKA_BOOTSTRAP)
    registry = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})
    publisher = AvroPublisher(registry)

    seed_from_config(publisher)
    tracked = TrackedAircraft(registry)
    tracked.refresh(timeout=2.0)  # initial full read of the compacted topic
    poller = Poller(settings, publisher)
    x_poller = XPostPoller(settings.x_bearer_token, publisher) if settings.x_bearer_token else None
    if x_poller is None:
        log.info("x_bearer_token not set in .env: X posts will not be polled")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    log.info("polling %d aircraft every %s (Ctrl+C to stop)", len(tracked.aircraft), _duration(settings.poll_seconds))
    # Wake up every TICK_SECONDS: positions are polled when POLL_SECONDS have passed, and X is
    # checked every tick so each day is picked as soon as it ends at midnight (a no-op otherwise).
    next_positions = 0.0
    while not stop.is_set():
        tracked.refresh()
        if time.time() >= next_positions:
            next_positions = time.time() + settings.poll_seconds
            for hex_code in list(tracked.aircraft):
                try:
                    poller.poll(hex_code)
                except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
                    # One failing API call shouldn't stop the loop or the other aircraft.
                    log.warning("%s: %s", hex_code, exc)
            publisher.flush()
            save_update_times(next_positions)
            log.info("next position update at %s", time.strftime("%H:%M", time.localtime(next_positions)))

        if x_poller:
            try:
                x_poller.poll(tracked.aircraft)
            except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
                log.warning("X posts: %s", exc)
        stop.wait(min(TICK_SECONDS, max(1.0, next_positions - time.time())))

    log.info("shutting down")
    publisher.flush()
    tracked.close()


if __name__ == "__main__":
    main()
