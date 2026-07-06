#!/bin/bash
# Éjszakai 40m FT8 — GUI + bridge, kiegyensúlyozott PRO, nem CQ üzem
# Csak a felügyelet hívja restart-ra (hiba esetén) — nem időzített újraindítás.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE="$ROOT/forgalminaplo/live"
DISPLAY="${DISPLAY:-:1}"

pkill -f run_ft8_gui.py 2>/dev/null || true
sleep 1

mkdir -p "$LIVE"
cd "$ROOT"
nohup env DISPLAY="$DISPLAY" .venv/bin/python scripts/run_ft8_gui.py >> "$LIVE/gui_nohup.log" 2>&1 &
sleep 6

pgrep -f ft8_live_bridge.py >/dev/null || \
  nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/ft8_live_bridge.py" >> "$LIVE/bridge_nohup.log" 2>&1 &

cat > "$LIVE/operator_in.txt" <<'EOF'
BAND 40m
DIAL 7.074
MAP_OFF
CQ_WAIT 1
CQ_MODE_OFF
PRO_ON
PRO_PRIORITY balanced
PTT_ON
START_RX
EOF

echo "Éjszakai 40m üzem indul — kiegyensúlyozott PRO, CQ üzem KI"
sleep 4
"$ROOT/.venv/bin/python" -c "
import json
d=json.load(open('$LIVE/gui_status.json'))
print(
  'band', d.get('band'), 'dial', d.get('dial_mhz'),
  'rx', d.get('rx_running'), 'ptt', d.get('ptt_armed'),
  'pro', d.get('pro_operator'), 'cq_only', d.get('cq_only_mode'),
  'priority', d.get('pro_priority'), 'phase', d.get('qso_phase'),
)
"
