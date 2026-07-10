#!/bin/bash
# FT8 teljes automata üzem — GUI + live bridge + felügyelet
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE="$ROOT/forgalminaplo/live"
DISPLAY="${DISPLAY:-:1}"

pkill -f run_ft8_gui.py 2>/dev/null || true
pkill -f ft8_live_bridge.py 2>/dev/null || true
pkill -f auto_ft8_watch.py 2>/dev/null || true
sleep 1

mkdir -p "$LIVE"
cd "$ROOT"
nohup env DISPLAY="$DISPLAY" .venv/bin/python scripts/run_ft8_gui.py >> "$LIVE/gui_nohup.log" 2>&1 &
sleep 5

pgrep -f ft8_live_bridge.py >/dev/null || \
  nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/ft8_live_bridge.py" >> "$LIVE/bridge_nohup.log" 2>&1 &

sleep 2
printf 'BAND 20m\nDIAL 14.074\nSTART_RX\nPRO_ON\nCQ_MODE_OFF\nPTT_ON\nABORT_QSO\n' > "$LIVE/operator_in.txt"

nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/auto_ft8_watch.py" >> "$LIVE/auto_watch_nohup.log" 2>&1 &

echo "FT8 auto üzem indul — GUI + PRO + PTT"
sleep 3
"$ROOT/.venv/bin/python" -c "
import json
d=json.load(open('$LIVE/gui_status.json'))
print('rx',d.get('rx_running'),'ptt',d.get('ptt_armed'),'pro',d.get('pro_operator'),'phase',d.get('qso_phase'))
"
