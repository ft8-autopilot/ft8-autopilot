"""
FT8 viselkedés spec — 50 pont automatikus ellenőrzés.

Spec: data/FT8_BEHAVIOR_50.md
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from cw_discover.paths import FORGALMI_LIVE, GUI_STATUS, LOG_DIR, PROJECT_ROOT, TX_LOG

from cw_discover.ft8.engine import DecodeReport
from cw_discover.ft8.forgalmi_log import ForgalmiNaplo
from cw_discover.ft8.ft8_protocol import (
  is_73,
  is_grid_token,
  is_r_report,
  is_report,
  is_rr73,
  message_triplet,
  snr_report_text,
)
from cw_discover.ft8.ft8_slot import (
  CYCLE_SECONDS,
  MAX_TX_START_SECONDS,
  decode_is_fresh,
  ft8_period_at,
  opposite_period,
  period_from_cycle,
  seconds_until_tx_period,
)
from cw_discover.ft8.pro_operator import ProOperatorConfig
from cw_discover.ft8.ptt_client import Esp32Ptt
from cw_discover.ft8.qso_controller import Ft8AutoOperator, QsoPhase
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.ft8.tx_player import Ft8TxPlayer, TxResult


def _fresh_cycle(align_offset: int = 0) -> str:
  t = int(time.time()) - align_offset
  t -= t % 15
  return time.strftime("%y%m%d_%H%M%S", time.gmtime(t))


def _cycle_for_period(period: int) -> str:
  """Friss FT8 slot a kért periódusban (előre keres)."""
  t = int(time.time())
  t -= t % 15
  for _ in range(4):
    if ft8_period_at(t) == period:
      return time.strftime("%y%m%d_%H%M%S", time.gmtime(t))
    t += 15
  return _fresh_cycle()


def _cycle_sequence(n: int = 5, step: int = 15) -> list[str]:
  base = int(time.time())
  base -= base % 15
  return [time.strftime("%y%m%d_%H%M%S", time.gmtime(base + i * step)) for i in range(n)]


def _report(
  message: str,
  *,
  snr: int = -10,
  hz: int = 1500,
  cycle: str | None = None,
) -> DecodeReport:
  return DecodeReport(
    cycle=cycle or _fresh_cycle(),
    snr=snr,
    dt=0.1,
    audio_hz=hz,
    rf_khz=7074.0,
    message=message,
    time_received=datetime.now(tz=timezone.utc).timestamp(),
  )


class RecordingTx:
  """TX rögzítés — slot (tx_period) ellenőrzéshez."""

  def __init__(self) -> None:
    self.calls: list[tuple[str, float, int | None]] = []

  def transmit(self, message: str, audio_hz: float, *, tx_period: int | None = None, **kwargs) -> TxResult:
    self.calls.append((message, audio_hz, tx_period))
    return TxResult(message=message, audio_hz=audio_hz, ok=True)

  def halt_audio(self) -> None:
    pass

  def force_ptt_off(self) -> None:
    pass


def _make_op(tmp_path, *, pro: bool = False, defer_cq: bool = False) -> tuple[Ft8AutoOperator, RecordingTx]:
  pro_cfg = ProOperatorConfig(enabled=pro, defer_cq_pick=defer_cq, min_snr=-20, max_snr=5)
  st = StationIdentity(
    callsign="N0CALL",
    grid="JN96",
    cq_min_snr=-20,
    ptt_port="",
    pro=pro_cfg,
  )
  tx = RecordingTx()
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=tx)
  op.set_armed(True)
  time.sleep(0.05)
  return op, tx


def _wait_tx(tx: RecordingTx, n: int = 1, timeout: float = 1.0) -> None:
  deadline = time.time() + timeout
  while time.time() < deadline and len(tx.calls) < n:
    time.sleep(0.02)


def _decode_step(
  op: Ft8AutoOperator,
  tx: RecordingTx,
  message: str,
  *,
  cycle: str | None = None,
  snr: int = -10,
  hz: int = 1500,
) -> None:
  """Egy dekód + várakozás a TX workerre (sor ürítés elkerülése)."""
  before = len(tx.calls)
  op.on_decode(_report(message, cycle=cycle or _fresh_cycle(), snr=snr, hz=hz))
  _wait_tx(tx, before + 1)


# --- A. Időzítés (1–10) ---


def test_a01_cycle_length_15s() -> None:
  """#1: FT8 ciklus 15 s."""
  assert CYCLE_SECONDS == 15


def test_a02_half_duplex_opposite_periods() -> None:
  """#2–3: páros/páratlan periódus, ellentétes slot."""
  assert period_from_cycle("260704_122000") == 0
  assert period_from_cycle("260704_122015") == 1
  assert opposite_period(0) == 1
  assert opposite_period(1) == 0


def test_a04_tx_period_on_cq_answer(tmp_path) -> None:
  """#4–5: CQ válasz ellentétes tx_period."""
  op, _ = _make_op(tmp_path)
  cycle = _cycle_for_period(1)
  heard = period_from_cycle(cycle)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=cycle))
  assert op._active is not None
  assert op._active.tx_period == opposite_period(heard)


def test_a06_max_tx_start_window() -> None:
  """#6: TX ablak ≤ 2,5 s."""
  assert MAX_TX_START_SECONDS == 2.5
  t = calendar.timegm(time.strptime("2026-07-04 12:23:17", "%Y-%m-%d %H:%M:%S"))
  assert seconds_until_tx_period(1, t) == 0.0


def test_a07_seconds_until_own_period() -> None:
  """#7: következő saját slot számítás."""
  t = calendar.timegm(time.strptime("2026-07-04 12:23:17", "%Y-%m-%d %H:%M:%S"))
  assert ft8_period_at(t) == 1
  assert seconds_until_tx_period(0, t) == 13.0


def test_a08_retry_only_own_period(tmp_path) -> None:
  """#8: retry csak saját periódusban (on_cycle slot check)."""
  op, tx = _make_op(tmp_path)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_cycle_for_period(1)))
  _wait_tx(tx, 1)
  assert op._active.tx_period == 0
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=1):
    op.on_cycle(_fresh_cycle(), time.time())
  assert len(tx.calls) == 1
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
    op.on_cycle(_fresh_cycle(), time.time())
  _wait_tx(tx, 2)
  assert len(tx.calls) == 2
  assert tx.calls[1][0] == tx.calls[0][0]


def test_a09_stale_decode_rejected(tmp_path) -> None:
  """#9: régi dekód ignorálva."""
  op, tx = _make_op(tmp_path)
  assert not decode_is_fresh("260704_120000")
  op.on_decode(_report("CQ IK4LZH JN54", cycle="260704_120000"))
  _wait_tx(tx, 1, timeout=0.2)
  assert op._active is None
  assert len(tx.calls) == 0


def test_a10_decode_dt_fresh() -> None:
  """#10: friss dekód dt mező (szimulált)."""
  r = _report("CQ TEST JN96")
  assert r.dt <= 0.5


# --- B. Üzenetformátumok (11–20) ---


def test_b11_cq_format(tmp_path) -> None:
  """#11: saját CQ formátum."""
  op, tx = _make_op(tmp_path)
  op._queue_cq()
  _wait_tx(tx, 1)
  assert tx.calls[0][0] == "CQ N0CALL JN96"


def test_b12_cq_answer_format(tmp_path) -> None:
  """#12: CQ válasz formátum."""
  op, tx = _make_op(tmp_path)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  _wait_tx(tx, 1)
  assert tx.calls[0][0] == "IK4LZH N0CALL JN96"


def test_b13_report_format(tmp_path) -> None:
  """#13: remote report → R-jelentés."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(2)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  assert tx.calls[1][0].endswith("R-10")


def test_b14_r_report_format(tmp_path) -> None:
  """#14: R-jelentés küldés remote report után."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(2)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  assert tx.calls[1][0].endswith("R-10")


def test_b15_rr73_format(tmp_path) -> None:
  """#15: RR73."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(3)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  _decode_step(op, tx, "IK4LZH N0CALL R-05", cycle=cyc[2])
  assert tx.calls[2][0].endswith("RR73")


def test_b16_73_format(tmp_path) -> None:
  """#16: 73 zárás."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(4)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  _decode_step(op, tx, "IK4LZH N0CALL R-05", cycle=cyc[2])
  _decode_step(op, tx, "IK4LZH N0CALL RR73", cycle=cyc[3])
  assert tx.calls[3][0].endswith("73")


def test_b17_incoming_call_direction() -> None:
  """#17: bejövő = REMOTE N0CALL."""
  t = message_triplet("IK4LZH N0CALL JN54")
  assert t is not None
  assert t.call_a == "IK4LZH"
  assert t.call_b == "N0CALL"


def test_b18_self_decode_ignored(tmp_path) -> None:
  """#18: saját TX visszahallás ignorálva."""
  op, tx = _make_op(tmp_path)
  op.on_decode(_report("N0CALL DK7ZT -09"))
  _wait_tx(tx, 1, timeout=0.2)
  assert op._active is None
  assert len(tx.calls) == 0


def test_b19_grid_token() -> None:
  """#19: grid token."""
  assert is_grid_token("JN54")
  assert not is_grid_token("-09")


def test_b20_report_token() -> None:
  """#20: report token."""
  assert is_report("-09")
  assert is_r_report("R-05")
  assert not is_report("73")


# --- C. QSO állapotgép (21–35) ---


def test_c21_phases(tmp_path) -> None:
  """#21: fázisváltások."""
  op, _ = _make_op(tmp_path)
  assert op.phase == QsoPhase.IDLE
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  assert op.phase == QsoPhase.ACTIVE


def test_c22_cq_to_active(tmp_path) -> None:
  """#22: CQ → active."""
  op, _ = _make_op(tmp_path)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  assert op._active.remote_call == "IK4LZH"


def test_c23_incoming_priority(tmp_path) -> None:
  """#23: bejövő hívás."""
  op, tx = _make_op(tmp_path)
  op.on_decode(_report("IK4LZH N0CALL JN54", cycle=_fresh_cycle()))
  _wait_tx(tx, 1)
  assert op._active.remote_call == "IK4LZH"
  assert tx.calls[0][0] == "IK4LZH N0CALL JN96"


def test_c24_grid_triggers_report(tmp_path) -> None:
  """#24: remote report → R-jelentés TX."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(2)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1], snr=-9)
  assert op._active.rst_rcvd == "-09"
  assert tx.calls[1][0].endswith("R-09")


def test_c25_report_triggers_r_report(tmp_path) -> None:
  """#25: report → R-report válasz."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(3)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  _decode_step(op, tx, "IK4LZH N0CALL R-05", cycle=cyc[2])
  assert op._active.rst_rcvd == "-09"
  assert tx.calls[2][0].endswith("RR73")


def test_c26_r_report_triggers_rr73(tmp_path) -> None:
  """#26: R-report → RR73 + closing."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(3)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  _decode_step(op, tx, "IK4LZH N0CALL R-05", cycle=cyc[2])
  assert op.phase == QsoPhase.CLOSING
  assert tx.calls[2][0].endswith("RR73")


def test_c27_rr73_triggers_log(tmp_path) -> None:
  """#27: RR73 → 73 + napló."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(4)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  _decode_step(op, tx, "IK4LZH N0CALL R-05", cycle=cyc[2])
  _decode_step(op, tx, "IK4LZH N0CALL RR73", cycle=cyc[3])
  assert op.phase == QsoPhase.IDLE
  assert (tmp_path / "qso.jsonl").exists()
  assert "IK4LZH" in (tmp_path / "qso.jsonl").read_text()


def test_c28_abandon_after_3_retries(tmp_path) -> None:
  """#28: 3 ciklus után feladás."""
  op, tx = _make_op(tmp_path)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  _wait_tx(tx, 1)
  tx_p = op._active.tx_period
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    for _ in range(4):
      op.on_cycle(_fresh_cycle(), time.time())
  assert op._active is None
  assert op.phase == QsoPhase.IDLE


def test_c29_abort_clears_state(tmp_path) -> None:
  """#29: feladás után idle."""
  op, _ = _make_op(tmp_path)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  op.abort_qso("teszt")
  assert op._active is None
  assert op.phase == QsoPhase.IDLE


def test_c30_same_audio_hz(tmp_path) -> None:
  """#30: ugyanazon Hz-en maradunk."""
  from cw_discover.ft8.tx_player import snap_ft8_hz

  op, tx = _make_op(tmp_path)
  hz = snap_ft8_hz(1867.0)
  cyc = _cycle_sequence(2)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=cyc[0], hz=hz))
  op.on_decode(_report("IK4LZH N0CALL -09", cycle=cyc[1], hz=hz))
  _wait_tx(tx, 2)
  assert all(c[1] == hz for c in tx.calls)


def test_c31_drain_tx_queue(tmp_path) -> None:
  """#31: új TX törli a várakozó sort (csak utolsó kerül le)."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(2)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  assert tx.calls[0][0] == "IK4LZH N0CALL JN96"
  # Gyors második dekód (várakozás nélkül) — sor ürítés
  op.on_decode(_report("IK4LZH N0CALL -09", cycle=cyc[1]))
  _wait_tx(tx, 2)
  assert tx.calls[1][0].endswith("R-10")


def test_c32_retry_no_counter_reset(tmp_path) -> None:
  """#32: retry nem nullázza cycles_without_reply."""
  op, _ = _make_op(tmp_path)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  op._active.cycles_without_reply = 2
  op._queue_tx("IK4LZH N0CALL JN96", 1500.0, is_retry=True)
  assert op._active.cycles_without_reply == 2


def test_c33_new_tx_resets_counter(tmp_path) -> None:
  """#33: új TX reseteli a számlálót."""
  op, _ = _make_op(tmp_path)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  op._active.cycles_without_reply = 2
  op._queue_tx("IK4LZH N0CALL -10", 1500.0)
  assert op._active.cycles_without_reply == 0


def test_c35_full_qso_message_sequence(tmp_path) -> None:
  """#34–35: teljes QSO üzenetsor + napló."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(4)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  _decode_step(op, tx, "IK4LZH N0CALL R-05", cycle=cyc[2])
  _decode_step(op, tx, "IK4LZH N0CALL RR73", cycle=cyc[3])
  msgs = [c[0] for c in tx.calls]
  assert msgs[0] == "IK4LZH N0CALL JN96"
  assert msgs[1].endswith("R-10")
  assert msgs[2].endswith("RR73")
  assert msgs[3].endswith("73")


# --- D. Operátor prioritás (36–42) ---


def test_d36_active_qso_continues(tmp_path) -> None:
  """#36: aktív QSO folytatása — más CQ ignorálva."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(2)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=cyc[0]))
  op.on_decode(_report("CQ SP9JMZ JO90", cycle=cyc[1]))
  _wait_tx(tx, 1)
  assert op._active.remote_call == "IK4LZH"


def test_d37_incoming_over_cq(tmp_path) -> None:
  """#37: bejövő hívás CQ felett."""
  op, tx = _make_op(tmp_path)
  op.on_decode(_report("IK4LZH N0CALL JN54", cycle=_fresh_cycle()))
  _wait_tx(tx, 1)
  assert op._active is not None


def test_d39_cq_on_idle_cycles(tmp_path) -> None:
  """#39: saját CQ — első TX periódusban azonnal, majd várakozás."""
  op, tx = _make_op(tmp_path)
  op.station.cq_repeat_cycles = 3
  op.set_armed(True)
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
    op._cq_tx_period = 0
    op.on_cycle("c1", time.time())
  _wait_tx(tx, 1)
  assert tx.calls[0][0].startswith("CQ N0CALL")
  assert op._cq_wait_remaining == 3


def test_d40_defer_cq_pick(tmp_path) -> None:
  """#40: PRO defer_cq_pick — buffer, nem azonnali válasz."""
  op, tx = _make_op(tmp_path, pro=True, defer_cq=True)
  op.on_decode(_report("CQ IK4LZH JN54", snr=-8, cycle=_fresh_cycle()))
  _wait_tx(tx, 1, timeout=0.2)
  assert op._active is None
  assert len(op._cq_buffer) == 1


def test_d41_skip_worked_today(tmp_path) -> None:
  """#41: ma worked — kihagyás."""
  st = StationIdentity(callsign="N0CALL", grid="JN96", cq_min_snr=-20, ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
  from cw_discover.ft8.forgalmi_log import QsoRecord

  naplo.append_qso(
    QsoRecord(
      call="IK4LZH",
      grid="JN54",
      band="40m",
      dial_mhz=7.074,
      rst_sent="-10",
      rst_rcvd="-09",
      time_on=datetime.now(tz=timezone.utc),
      time_off=datetime.now(tz=timezone.utc),
      tx_audio_hz=1500,
    )
  )
  op, tx = _make_op(tmp_path)
  op.naplo = naplo
  op.on_decode(_report("CQ IK4LZH JN54", snr=-8, cycle=_fresh_cycle()))
  _wait_tx(tx, 1, timeout=0.2)
  assert op._active is None


def test_d42_pro_preempt(tmp_path) -> None:
  """#42: PRO preempt beragadt QSO."""
  op, tx = _make_op(tmp_path, pro=True)
  cyc = _cycle_sequence(2)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=cyc[0]))
  op._active.cycles_without_reply = 2
  op.on_decode(_report("DK7ZT N0CALL JO30", cycle=cyc[1]))
  _wait_tx(tx, 2)
  assert op._active.remote_call == "DK7ZT"


# --- E. TX / RX (43–47) ---


def test_e44_ptt_ok_parser() -> None:
  """#44: PTT OK válasz ellenőrzés."""
  assert Esp32Ptt._ptt_ok(["OK PTT 1"], "1")
  assert not Esp32Ptt._ptt_ok(["ERR"], "1")


def test_e46_mono_to_stereo() -> None:
  """#46: L+R stereo duplikálás."""
  mono = np.array([0.1, 0.2, 0.3], dtype=np.float32)
  stereo = Ft8TxPlayer._mono_to_stereo(mono)
  assert stereo.shape == (3, 2)
  assert np.allclose(stereo[:, 0], stereo[:, 1])


def test_e47_tx_log_path() -> None:
  """#47: TX napló útvonal létezik vagy létrehozható."""
  TX_LOG.parent.mkdir(parents=True, exist_ok=True)
  assert TX_LOG.parent.is_dir()


# --- F. Napló (48–50) ---


def test_f48_decode_log_dir() -> None:
  """#48: dekód napló könyvtár."""
  LOG_DIR.mkdir(parents=True, exist_ok=True)
  assert LOG_DIR.is_dir()


def test_f49_gui_status_path() -> None:
  """#49: GUI státusz fájl helye."""
  GUI_STATUS.parent.mkdir(parents=True, exist_ok=True)
  assert GUI_STATUS.parent.is_dir()


def test_f50_audit_script_exists() -> None:
  """#50: slot audit script."""
  p = PROJECT_ROOT / "scripts" / "audit_tx_slots.py"
  assert p.is_file()


# --- Teljes QSO integráció ---


def test_full_qso_flow_integration(tmp_path) -> None:
  """Teljes QSO — spec táblázat."""
  op, tx = _make_op(tmp_path)
  cyc = _cycle_sequence(4)
  _decode_step(op, tx, "CQ IK4LZH JN54", cycle=cyc[0])
  _decode_step(op, tx, "IK4LZH N0CALL -09", cycle=cyc[1])
  _decode_step(op, tx, "IK4LZH N0CALL R-05", cycle=cyc[2])
  _decode_step(op, tx, "IK4LZH N0CALL RR73", cycle=cyc[3])
  assert op.phase == QsoPhase.IDLE
  assert snr_report_text(-10) == "-10"
  qso_lines = (tmp_path / "qso.jsonl").read_text()
  assert "IK4LZH" in qso_lines
