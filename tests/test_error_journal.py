"""Hibanapló katalógus és journal tesztek."""
from __future__ import annotations

import json

from cw_discover.gui.error_catalog import ALL_CODES, CATALOG, classify_tx_error
from cw_discover.gui.error_journal import (
  MAX_ENTRIES,
  ErrorJournal,
  bind_error_journal,
  report_error,
  report_tx_error,
)


def test_catalog_covers_all_realistic_codes() -> None:
  assert len(ALL_CODES) == len(CATALOG)
  assert len(ALL_CODES) >= 20


def test_ring_buffer_drops_oldest(tmp_path) -> None:
  path = tmp_path / "err.json"
  j = ErrorJournal(path)
  bind_error_journal(j)
  for i in range(MAX_ENTRIES + 5):
    report_error("esp_ping_fail", f"ping-{i}", dedup=False)
  assert j.count == MAX_ENTRIES


def test_all_codes_injectable(tmp_path) -> None:
  path = tmp_path / "err.json"
  j = ErrorJournal(path)
  bind_error_journal(j)
  for code in ALL_CODES:
    report_error(code, f"teszt: {code}", dedup=False)
  codes = j.codes_recorded()
  assert codes == set(ALL_CODES)
  assert j.count == len(ALL_CODES)


def test_classify_safety_lock() -> None:
  assert classify_tx_error("PTT 1 nincs OK: ['ERR SAFETY_LOCK']") == "esp_safety_lock"


def test_report_tx_error_uses_catalog(tmp_path) -> None:
  j = ErrorJournal(tmp_path / "e.json")
  bind_error_journal(j)
  report_tx_error("CQ N0CALL", "encode_failed")
  e = j.entries_newest_first()[0]
  assert e.code == "tx_encode_fail"
  assert e.hint


def test_persist_includes_code(tmp_path) -> None:
  path = tmp_path / "err.json"
  j = ErrorJournal(path)
  bind_error_journal(j)
  report_error("audio_pactl_timeout", "list sinks", dedup=False)
  raw = json.loads(path.read_text(encoding="utf-8"))
  assert raw["entries"][-1]["code"] == "audio_pactl_timeout"
