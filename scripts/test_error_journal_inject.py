#!/usr/bin/env python3
"""Hibanapló injektálás — minden katalógus-kód + ellenőrzés."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cw_discover.gui.error_catalog import ALL_CODES, CATALOG
from cw_discover.paths import ERROR_JOURNAL, FORGALMI_LIVE

LIVE = FORGALMI_LIVE
OP = LIVE / "operator_in.txt"
GUI_STATUS = LIVE / "gui_status.json"


def _wait_file(path: Path, timeout: float = 8.0) -> bool:
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if path.exists() and path.stat().st_size > 0:
      return True
    time.sleep(0.2)
  return False


def _inject_via_gui_running() -> bool:
  if not OP.parent.exists():
    OP.parent.mkdir(parents=True, exist_ok=True)
  OP.write_text("ERROR_INJECT_ALL\n", encoding="utf-8")
  if not _wait_file(ERROR_JOURNAL, 15.0):
    return False
  time.sleep(2)
  try:
    data = json.loads(ERROR_JOURNAL.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return False
  entries = data.get("entries", [])
  codes = {e.get("code") for e in entries if e.get("code")}
  missing = set(ALL_CODES) - codes
  print(f"bejegyzések: {len(entries)}")
  print(f"kódok: {len(codes)} / {len(ALL_CODES)}")
  if missing:
    print("HIÁNYZÓ:", ", ".join(sorted(missing)))
    return False
  for code in ALL_CODES[:5]:
    spec = CATALOG[code]
    print(f"  OK {code}: {spec.title[:50]}…")
  print(f"  … és még {len(ALL_CODES) - 5} kód")
  return True


def _inject_headless() -> bool:
  from PyQt5 import QtWidgets

  from cw_discover.gui.ft8_window import Ft8Window

  if ERROR_JOURNAL.exists():
    ERROR_JOURNAL.unlink()
  app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
  w = Ft8Window()
  w._inject_all_errors_test()
  codes = w._error_journal.codes_recorded()
  missing = set(ALL_CODES) - codes
  print(f"headless bejegyzések: {w._error_journal.count}")
  if missing:
    print("HIÁNYZÓ:", ", ".join(sorted(missing)))
    return False
  for entry in w._error_journal.entries_newest_first()[:3]:
    print(entry.format_block())
    print("---")
  return True


def main() -> int:
  print("=== Hibanapló injektálás ===")
  if _inject_via_gui_running():
    print("ÉLŐ GUI injektálás: OK")
    return 0
  print("Élő GUI nem válaszolt — headless fallback")
  if _inject_headless():
    print("HEADLESS injektálás: OK")
    return 0
  return 1


if __name__ == "__main__":
  raise SystemExit(main())
