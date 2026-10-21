#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

# Render config/kafka.properties with log.dirs pointing at <project>/.kafka-data
# (the default /tmp location is wiped when macOS reboots).
mkdir -p "$KAFKA_DATA_DIR"
CONFIG="$LOG_DIR/kafka.rendered.properties"
sed "s#^log.dirs=.*#log.dirs=$KAFKA_DATA_DIR#" "$PROJECT_ROOT/config/kafka.properties" > "$CONFIG"

# KRaft needs its storage formatted once with a cluster ID before the first start.
if [[ ! -f "$KAFKA_DATA_DIR/meta.properties" ]]; then
  echo "First run: formatting KRaft storage in $KAFKA_DATA_DIR"
  kafka-storage format --standalone -t "$(kafka-storage random-uuid)" -c "$CONFIG"
fi

kafka-server-start -daemon "$CONFIG"
wait_for_port 9092 "Kafka"
