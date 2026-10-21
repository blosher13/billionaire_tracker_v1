"""One-off: publish the most recent original X post of every billionaire in config/billionaires.csv.

    python -m producer.backfill_posts

The posts go through the normal pipeline (x.posts → Streams → x.posts.located → Postgres), so
each is placed at the jet's last known position at the time it was posted. Running it again
is harmless (Postgres upserts by post id) but spends another ~$0.025 per account.
"""

import logging

from confluent_kafka.schema_registry import SchemaRegistryClient

from producer.config import KAFKA_BOOTSTRAP, SCHEMA_REGISTRY_URL, Settings, load_aircraft
from producer.producer import AvroPublisher
from producer.topics import ensure_topics
from producer.x_posts import XPostPoller


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    settings = Settings.from_env()
    if not settings.x_bearer_token:
        raise SystemExit("x_bearer_token (or twitter_beartoken) is not set in .env")
    ensure_topics(KAFKA_BOOTSTRAP)
    publisher = AvroPublisher(SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL}))
    XPostPoller(settings.x_bearer_token, publisher).backfill_latest(load_aircraft())


if __name__ == "__main__":
    main()
