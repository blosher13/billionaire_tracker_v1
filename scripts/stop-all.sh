#!/usr/bin/env bash
# Stop in reverse dependency order: clients of Kafka first, Kafka last.
source "$(dirname "$0")/env.sh"

pkill -f org.apache.kafka.connect.cli.ConnectDistributed || true
schema-registry-stop || true
kafka-server-stop || true
echo "Stopped."
