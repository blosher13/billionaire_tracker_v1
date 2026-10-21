#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

nc -z localhost 9092 || { echo "Kafka is not running — run scripts/start-kafka.sh first" >&2; exit 1; }

schema-registry-start -daemon "$PROJECT_ROOT/config/schema-registry.properties"
wait_for_port 8081 "Schema Registry"
