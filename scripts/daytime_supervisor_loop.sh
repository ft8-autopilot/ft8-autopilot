#!/bin/bash
# Óránkénti FT8 nappali felügyelet — 16:30 CEST-ig, mint az éjszakai stack.
# Fagyás / leállás / helytelen állapot → soft_fix vagy hard restart (ft8_night_health.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE="$ROOT/forgalminaplo/live"
LOG="$LIVE/daytime_supervisor_loop.log"
PIDFILE="$LIVE/daytime_supervisor.pid"
PY="$ROOT/.venv/bin/python"
INTERVAL="${FT8_SUPERVISOR_INTERVAL_SEC:-3600}"
DEADLINE_LOCAL="${FT8_SUPERVISOR_DEADLINE_LOCAL:-2026-07-06 16:30:00}"
TZ_NAME="${FT8_SUPERVISOR_TZ:-Europe/Budapest}"

mkdir -p "$LIVE"
cd "$ROOT"

deadline_epoch() {
  TZ="$TZ_NAME" date -d "$DEADLINE_LOCAL" +%s 2>/dev/null \
    || TZ="$TZ_NAME" date -j -f "%Y-%m-%d %H:%M:%S" "$DEADLINE_LOCAL" +%s
}

DEADLINE_EPOCH="$(deadline_epoch)"
NOW_EPOCH="$(date +%s)"
if [ "$NOW_EPOCH" -ge "$DEADLINE_EPOCH" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] daytime supervisor: deadline már elmúlt ($DEADLINE_LOCAL $TZ_NAME)" | tee -a "$LOG"
  exit 0
fi

echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"; echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] daytime supervisor leáll (trap)" | tee -a "$LOG"' EXIT INT TERM

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] daytime supervisor indul (interval=${INTERVAL}s, deadline=$DEADLINE_LOCAL $TZ_NAME, pid=$$)" | tee -a "$LOG"

while true; do
  NOW_EPOCH="$(date +%s)"
  if [ "$NOW_EPOCH" -ge "$DEADLINE_EPOCH" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] daytime supervisor: deadline elérve ($DEADLINE_LOCAL $TZ_NAME) — kilépés" | tee -a "$LOG"
    exit 0
  fi
  REMAIN=$((DEADLINE_EPOCH - NOW_EPOCH))
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] --- tick (hátralévő ${REMAIN}s) ---" | tee -a "$LOG"
  if "$PY" "$ROOT/scripts/ft8_night_health.py" 2>&1 | tee -a "$LOG"; then
    :
  else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARN health exit=$?" | tee -a "$LOG"
  fi
  NOW_EPOCH="$(date +%s)"
  if [ "$NOW_EPOCH" -ge "$DEADLINE_EPOCH" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] daytime supervisor: deadline tick után — kilépés" | tee -a "$LOG"
    exit 0
  fi
  SLEEP_FOR="$INTERVAL"
  REMAIN=$((DEADLINE_EPOCH - NOW_EPOCH))
  if [ "$REMAIN" -lt "$SLEEP_FOR" ]; then
    SLEEP_FOR="$REMAIN"
  fi
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] következő tick ${SLEEP_FOR}s múlva" | tee -a "$LOG"
  sleep "$SLEEP_FOR"
done
