#!/bin/bash
# Félóránkénti FT8 éjszakai felügyelet — fagyás, leállás, helyreállítás
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE="$ROOT/forgalminaplo/live"
LOG="$LIVE/overnight_supervisor_loop.log"
PY="$ROOT/.venv/bin/python"
INTERVAL="${FT8_SUPERVISOR_INTERVAL_SEC:-1800}"

mkdir -p "$LIVE"
cd "$ROOT"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] overnight supervisor loop indul (interval=${INTERVAL}s)" | tee -a "$LOG"

while true; do
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] --- tick ---" | tee -a "$LOG"
  if "$PY" "$ROOT/scripts/ft8_night_health.py" 2>&1 | tee -a "$LOG"; then
    :
  else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARN night_health exit=$?" | tee -a "$LOG"
  fi
  sleep "$INTERVAL"
done
