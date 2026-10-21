#!/usr/bin/env bash
# Create or update every connector in connect/*.json (connector name = file name).
# PUT .../config is idempotent: safe to re-run after editing a config.
set -euo pipefail
source "$(dirname "$0")/env.sh"

for file in "$PROJECT_ROOT"/connect/*.json; do
  name="$(basename "$file" .json)"
  sed "s#__PROJECT_ROOT__#$PROJECT_ROOT#g" "$file" |
    curl -sS -X PUT -H "Content-Type: application/json" --data @- \
      "http://localhost:8083/connectors/$name/config" > /dev/null
  echo "registered $name"
done

sleep 3
for file in "$PROJECT_ROOT"/connect/*.json; do
  name="$(basename "$file" .json)"
  curl -sS "http://localhost:8083/connectors/$name/status"
  echo
done
