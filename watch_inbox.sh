#!/usr/bin/env bash
# inotifywait on jobs/incoming -> python run.py
# requires: inotify-tools
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INCOMING="${ROOT}/jobs/incoming"
PY="${ROOT}/.venv/bin/python"
LOG="${ROOT}/logs/watch.log"

# shellcheck disable=SC1091
if [[ -f /etc/sde/env ]]; then
  set -a
  source /etc/sde/env
  set +a
fi

mkdir -p "$INCOMING" "${ROOT}/logs"

if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

if ! command -v inotifywait >/dev/null 2>&1; then
  echo "inotifywait not found (install inotify-tools)" >&2
  exit 1
fi

run_inbox() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) run.py" >>"$LOG"
  flock "${ROOT}/logs/run.lock" "$PY" "${ROOT}/run.py" >>"$LOG" 2>&1 || true
}

run_inbox

echo "watching ${INCOMING}"
inotifywait -m -e close_write,moved_to --format '%f' "$INCOMING" | while read -r file; do
  case "$file" in
    *.json)
      sleep 1
      run_inbox
      ;;
  esac
done
