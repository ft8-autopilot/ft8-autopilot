"""Forgalmi napló javítás — tx.log RST visszakeresés."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cw_discover.ft8.forgalmi_repair import (
  is_test_call,
  load_tx_starts,
  repair_qso_records,
  rst_sent_from_tx_log,
)


def test_is_test_call_loop() -> None:
  assert is_test_call("LOOP1")
  assert is_test_call("loop2")
  assert not is_test_call("OE7JUM")


def test_rst_sent_from_tx_log_oe7jum(tmp_path: Path) -> None:
  tx = tmp_path / "tx.log"
  tx.write_text(
    "\n".join(
      [
        "2026-07-04T14:26:30.011745+00:00 TX_START OE7JUM N0CALL JN96 | 328 Hz p0",
        "2026-07-04T14:27:30.000171+00:00 TX_START OE7JUM N0CALL R-17 | 328 Hz p0",
        "2026-07-04T14:28:30.000293+00:00 TX_START OE7JUM N0CALL 73 | 328 Hz p0",
      ]
    ),
    encoding="utf-8",
  )
  tx_starts = load_tx_starts(tx)
  t_on = datetime(2026, 7, 4, 14, 26, 10, tzinfo=timezone.utc)
  t_off = datetime(2026, 7, 4, 14, 27, 57, tzinfo=timezone.utc)
  assert rst_sent_from_tx_log(
    call="OE7JUM", time_on=t_on, time_off=t_off, tx_starts=tx_starts, me="N0CALL"
  ) == "-17"


def test_repair_drops_loop_and_fixes_rst(tmp_path: Path) -> None:
  tx = tmp_path / "tx.log"
  tx.write_text(
    "2026-07-04T15:06:30.000171+00:00 TX_START SP2OSA N0CALL R-09 | 1501 Hz p0\n",
    encoding="utf-8",
  )
  records = [
    {
      "qso_id": "loop-id",
      "call": "LOOP1",
      "time_on_iso": "2026-07-04T14:40:30+00:00",
      "time_off_iso": "2026-07-04T14:41:30+00:00",
      "rst_sent": "+00",
      "rst_rcvd": "+00",
      "adif_blob": {"rst_sent": "+00"},
    },
    {
      "qso_id": "real-id",
      "call": "SP2OSA",
      "time_on_iso": "2026-07-04T15:06:10+00:00",
      "time_off_iso": "2026-07-04T15:07:27+00:00",
      "rst_sent": "+00",
      "rst_rcvd": "-11",
      "adif_blob": {"rst_sent": "+00", "rst_rcvd": "-11"},
    },
  ]
  repaired, notes = repair_qso_records(records, tx_starts=load_tx_starts(tx))
  assert len(repaired) == 1
  assert repaired[0]["call"] == "SP2OSA"
  assert repaired[0]["rst_sent"] == "-09"
  assert any("LOOP1" in n for n in notes)
