"""Projekt útvonalak — ft8-autopilot 2.0 gyökér alatt minden futásidejű adat."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORGALMI_DIR = PROJECT_ROOT / "forgalminaplo"
FORGALMI_LIVE = FORGALMI_DIR / "live"
LOG_DIR = PROJECT_ROOT / "logs"
STATE_DIR = PROJECT_ROOT / "state"

STATION_FILE = FORGALMI_DIR / "station.json"
TX_LOG = FORGALMI_LIVE / "tx.log"
GUI_STATUS = FORGALMI_LIVE / "gui_status.json"
SAFETY_STATE = FORGALMI_LIVE / "safety_state.json"
OPERATOR_IN = FORGALMI_LIVE / "operator_in.txt"
OPERATOR_OUT = FORGALMI_LIVE / "operator_out.log"
ERROR_JOURNAL = FORGALMI_LIVE / "error_journal.json"
