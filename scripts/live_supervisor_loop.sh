#!/usr/bin/env bash
# 30 perc percenkénti FT8 élő felügyelet
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
TICKS="${1:-30}"
INTERVAL="${2:-60}"
cd "$ROOT"
for i in $(seq 1 "$TICKS"); do
  sleep "$INTERVAL"
  echo "AGENT_LOOP_TICK_FT8_SUPERVISOR {\"tick\":$i,\"of\":$TICKS,\"prompt\":\"live supervisor tick $i/$TICKS\"}"
  "$PY" scripts/live_supervisor_tick.py 2>&1 | tail -3
done
echo "AGENT_LOOP_TICK_FT8_SUPERVISOR {\"tick\":done,\"prompt\":\"supervisor loop finished $TICKS ticks\"}"
