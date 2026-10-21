"""Create the project's Kafka topics if they don't exist yet.

Kafka would auto-create the data topics on first write, but tracked.aircraft must be
*compacted* (Kafka keeps only the latest value per hex, forever), which auto-create
can't do. Creating them all here also keeps partition counts equal, which the Streams
join requires ("co-partitioning").
"""

import logging

from confluent_kafka.admin import AdminClient, NewTopic

from producer.config import (
    TOPIC_ENRICHED,
    TOPIC_POSITIONS,
    TOPIC_TRACKED,
    TOPIC_WEATHER,
    TOPIC_X_POSTS,
    TOPIC_X_POSTS_LOCATED,
)

PARTITIONS = 1  # single local broker; every joined topic must use the same count
REPLICATION = 1

TOPIC_CONFIGS = {
    TOPIC_POSITIONS: {},
    TOPIC_WEATHER: {},
    TOPIC_ENRICHED: {},
    TOPIC_TRACKED: {"cleanup.policy": "compact"},
    TOPIC_X_POSTS: {},
    TOPIC_X_POSTS_LOCATED: {},
}


def ensure_topics(bootstrap: str) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap, "broker.address.family": "v4"})
    existing = set(admin.list_topics(timeout=10).topics)

    missing = [
        NewTopic(name, num_partitions=PARTITIONS, replication_factor=REPLICATION, config=cfg)
        for name, cfg in TOPIC_CONFIGS.items()
        if name not in existing
    ]
    if not missing:
        return

    for name, future in admin.create_topics(missing).items():
        future.result()  # raises if creation failed
        logging.getLogger("topics").info("created topic %s", name)
