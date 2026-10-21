"""Background Kafka consumers that feed the API.

* flight.enriched, x.posts.located → pushed to every WebSocket client watching that aircraft
* tracked.aircraft → the current set of tracked aircraft (reuses the producer's TrackedAircraft)

confluent-kafka's consumer is blocking, so it runs in its own thread and hands records to the
asyncio event loop with call_soon_threadsafe.
"""

import asyncio
import logging
import threading
from datetime import datetime, timezone

from confluent_kafka import OFFSET_END, Consumer, TopicPartition
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

from producer.config import KAFKA_CLIENT, TOPIC_ENRICHED, TOPIC_X_POSTS_LOCATED
from producer.producer import TrackedAircraft

log = logging.getLogger("api.live")

# Topic → message type sent to WebSocket clients.
LIVE_TOPICS = {TOPIC_ENRICHED: "flight", TOPIC_X_POSTS_LOCATED: "post"}


def jsonable(record: dict) -> dict:
    """Make a record JSON-friendly: datetimes become ISO-8601 UTC strings."""
    return {k: _iso(v) if isinstance(v, datetime) else v for k, v in record.items()}


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:  # Postgres columns are timestamp-without-time-zone, written in UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LiveFeed:
    def __init__(self, registry: SchemaRegistryClient):
        self.tracked = TrackedAircraft(registry, group_id="billiontracker-api")

        # Start at the END of the live topics: clients only want new records (history comes
        # from Postgres). assign() instead of subscribe() means no group rebalance on startup
        # and no committed offsets to manage.
        self._consumer = Consumer({
            **KAFKA_CLIENT,
            "group.id": "billiontracker-api",
            "enable.auto.commit": False,
        })
        metadata = self._consumer.list_topics(timeout=10).topics
        self._consumer.assign([
            TopicPartition(topic, p, OFFSET_END) for topic in LIVE_TOPICS for p in metadata[topic].partitions
        ])
        self._deserialize = AvroDeserializer(registry)

        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-feed", daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- called from the event loop -------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.tracked.refresh(timeout=2.0)  # initial read of the compacted topic
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._consumer.close()
        self.tracked.close()

    # Subscribing to ALL receives records for every aircraft (the homepage's fleet map).
    ALL = "*"

    def subscribe(self, hex_code: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.setdefault(hex_code, set()).add(queue)
        return queue

    def unsubscribe(self, hex_code: str, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.get(hex_code, set()).discard(queue)

    # --- background thread -----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tracked.refresh(timeout=0)  # pick up aircraft added/removed via the API
            msg = self._consumer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                log.warning("%s: %s", msg.topic(), msg.error())
                continue

            record = jsonable(self._deserialize(msg.value(), SerializationContext(msg.topic(), MessageField.VALUE)))
            item = (LIVE_TOPICS[msg.topic()], record)
            with self._lock:
                queues = [*self._subscribers.get(record["hex"], ()), *self._subscribers.get(self.ALL, ())]
            for queue in queues:
                self._loop.call_soon_threadsafe(_offer, queue, item)


def _offer(queue: asyncio.Queue, item: tuple[str, dict]) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass  # a slow client misses an update rather than blocking everyone
