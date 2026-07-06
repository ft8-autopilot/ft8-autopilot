#!/bin/bash
# Éjszakai FT8 felügyelet — 30 percenként tick reggel 8:00-ig (Europe/Budapest)
set -euo pipefail

END_LOCAL="2026-07-05 08:00:00"
END_EPOCH=$(TZ=Europe/Budapest date -d "$END_LOCAL" +%s 2>/dev/null || date -d "2026-07-05 06:00:00 UTC" +%s)

echo "[night_loop] indul, vége: $END_LOCAL (epoch $END_EPOCH)"

while true; do
  NOW=$(date +%s)
  if [ "$NOW" -ge "$END_EPOCH" ]; then
    echo 'AGENT_LOOP_DONE_ft8_night {"reason":"8:00 elérve"}'
    exit 0
  fi
  sleep 1800
  NOW=$(date +%s)
  if [ "$NOW" -ge "$END_EPOCH" ]; then
    echo 'AGENT_LOOP_DONE_ft8_night {"reason":"8:00 elérve"}'
    exit 0
  fi
  echo 'AGENT_LOOP_TICK_ft8_night {"prompt":"FT8 éjszakai felügyelet: futtasd ./scripts/ft8_night_health.py ; ha verdict=ok vagy soft, ne nyúlj máshoz; ha restart volt, ellenőrizd gui_status-t."}'
done
