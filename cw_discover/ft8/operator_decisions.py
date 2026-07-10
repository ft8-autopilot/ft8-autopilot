"""Operátori döntés napló — preempt, abandon, CQ választás (JSONL)."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cw_discover.paths import FORGALMI_LIVE

_DECISION_LOG = Path(
  os.environ.get("FT8_OPERATOR_DECISIONS", str(FORGALMI_LIVE / "operator_decisions.jsonl"))
)
_MAX_DECISION_LOG_BYTES = 2_000_000
_lock = threading.Lock()


def _maybe_rotate_decision_log() -> None:
  """~2 MB felett archívum — élő fájl marad kicsi."""
  try:
    if not _DECISION_LOG.is_file():
      return
    if _DECISION_LOG.stat().st_size <= _MAX_DECISION_LOG_BYTES:
      return
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive = _DECISION_LOG.with_name(f"operator_decisions_{stamp}.jsonl")
    _DECISION_LOG.rename(archive)
  except OSError:
    pass


def log_operator_decision(event: str, **fields: Any) -> None:
  """Append-only JSONL — hibatűrő, nem blokkolja a QSO útvonalat."""
  _maybe_rotate_decision_log()
  row = {
    "time_utc": datetime.now(tz=timezone.utc).isoformat(),
    "event": event,
    **fields,
  }
  try:
    _DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
      with _DECISION_LOG.open("a", encoding="utf-8") as f:
        f.write(line)
  except OSError:
    pass


def decision_log_path() -> Path:
  return _DECISION_LOG
