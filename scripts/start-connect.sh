#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

nc -z localhost 8081 || { echo "Schema Registry is not running — run scripts/start-schema-registry.sh first" >&2; exit 1; }

# Fill in the absolute paths the worker config needs.
CONFIG="$LOG_DIR/connect.rendered.properties"
sed -e "s#__PROJECT_ROOT__#$PROJECT_ROOT#g" -e "s#__CONFLUENT_HOME__#$CONFLUENT_HOME#g" \
  "$PROJECT_ROOT/config/connect-distributed.properties" > "$CONFIG"

connect-distributed -daemon "$CONFIG"
wait_for_port 8083 "Kafka Connect" 120
