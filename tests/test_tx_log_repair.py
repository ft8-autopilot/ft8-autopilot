"""tx.log javítás tesztek."""
from __future__ import annotations

from cw_discover.ft8.tx_log_repair import repair_tx_log


def test_repair_tx_log_strips_null_bytes(tmp_path) -> None:
  p = tmp_path / "tx.log"
  p.write_bytes(b"2026-07-06T17:00:00 TX_OK CQ N0CALL JN96\n\x00\x00")
  n = repair_tx_log(p)
  assert n == 2
  text = p.read_text(encoding="utf-8")
  assert "\x00" not in text
  assert "TX_OK CQ N0CALL JN96" in text


def test_repair_tx_log_noop_when_clean(tmp_path) -> None:
  p = tmp_path / "tx.log"
  p.write_text("ok line\n", encoding="utf-8")
  assert repair_tx_log(p) == 0
