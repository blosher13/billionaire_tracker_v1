# Sourced by the other scripts (and handy in your own shell: `source scripts/env.sh`).
export CONFLUENT_HOME="${CONFLUENT_HOME:-$HOME/confluent-8.3.2}"
export PATH="$CONFLUENT_HOME/bin:$PATH"

export PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export KAFKA_DATA_DIR="$PROJECT_ROOT/.kafka-data"
# Confluent's start scripts write their logs to $LOG_DIR when run with -daemon.
export LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# Block until something is listening on localhost:<port>, or give up after <seconds>.
wait_for_port() {
  local port=$1 name=$2 timeout=${3:-60}
  for ((i = 0; i < timeout; i++)); do
    if nc -z localhost "$port" 2>/dev/null; then
      echo "$name is up on :$port"
      return 0
    fi
    sleep 1
  done
  echo "$name did not start on :$port within ${timeout}s — check $LOG_DIR" >&2
  return 1
}
