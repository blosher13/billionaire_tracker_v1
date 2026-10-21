#!/usr/bin/env bash
# Serve the read-only site to the internet from this Mac through a Cloudflare Tunnel.
#
#   ./scripts/start-public.sh            # quick tunnel: random https://<words>.trycloudflare.com URL
#   CLOUDFLARE_TUNNEL=billiontracker ./scripts/start-public.sh
#                                        # named tunnel on your own domain (needs a Cloudflare account)
#
# Only the API on localhost:8000 is exposed. Kafka, Schema Registry, Connect and Postgres stay
# private. No router ports are opened: cloudflared makes an outbound connection to Cloudflare.
# Ctrl+C stops the tunnel (the site goes offline); the API keeps running.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"

command -v cloudflared >/dev/null || { echo "Install cloudflared first: brew install cloudflared" >&2; exit 1; }

for port_name in "9092 Kafka" "8081 Schema-Registry" "8083 Kafka-Connect"; do
  set -- $port_name
  nc -z localhost "$1" 2>/dev/null || { echo "$2 is not running (port $1) — start the infrastructure first (README → Run)" >&2; exit 1; }
done
pgrep -f "producer.producer" >/dev/null || echo "Warning: the producer isn't running, so no new data will arrive"
pgrep -f "billiontracker.EnricherApp" >/dev/null || echo "Warning: the Streams app isn't running, so no new data will arrive"

# The API: read-only, bound to localhost only (cloudflared connects to it locally).
if ! nc -z localhost 8000 2>/dev/null; then
  (cd web && npm run build --silent)
  READ_ONLY=true nohup "$PROJECT_ROOT/.virtual_env/bin/uvicorn" api.main:app --host 127.0.0.1 --port 8000 \
    --no-server-header >> "$LOG_DIR/api.log" 2>&1 &
  wait_for_port 8000 "API (read-only)"
fi
if curl -s localhost:8000/api/config | grep -q '"read_only":false'; then
  echo "The running API is in admin mode (READ_ONLY=false). Stop it and re-run this script." >&2
  exit 1
fi

# caffeinate keeps the Mac from sleeping while the tunnel runs (-s applies when on AC power).
if [[ -n "${CLOUDFLARE_TUNNEL:-}" ]]; then
  echo "Starting named tunnel '$CLOUDFLARE_TUNNEL'…"
  exec caffeinate -is cloudflared tunnel --no-autoupdate run "$CLOUDFLARE_TUNNEL"
else
  echo "Starting a quick tunnel. Your public URL is the https://….trycloudflare.com line below."
  echo "Add that URL to your Google Maps key's allowed websites, or the map won't load."
  exec caffeinate -is cloudflared tunnel --no-autoupdate --url http://localhost:8000
fi
