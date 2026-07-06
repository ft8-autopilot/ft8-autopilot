"""QSO állapotgép tesztek — szimulált TX."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from cw_discover.ft8.engine import DecodeReport
from cw_discover.ft8.forgalmi_log import ForgalmiNaplo
from cw_discover.ft8.qso_controller import Ft8AutoOperator, QsoPhase
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.ft8.tx_player import Ft8TxPlayer


def _fresh_cycle(align_offset: int = 0) -> str:
  t = int(time.time()) - align_offset
  t -= t % 15
  return time.strftime("%y%m%d_%H%M%S", time.gmtime(t))


def _report(message: str, snr: int = -10, hz: int = 1500, cycle: str | None = None) -> DecodeReport:
  return DecodeReport(
    cycle=cycle or _fresh_cycle(),
    snr=snr,
    dt=0.1,
    audio_hz=hz,
    rf_khz=7074.0,
    message=message,
    time_received=datetime.now(tz=timezone.utc).timestamp(),
  )


def test_answer_cq(tmp_path) -> None:
  st = StationIdentity(
    callsign="N0CALL",
    grid="JN96",
    cq_min_snr=-20,
    ptt_port="",
  )
  naplo = ForgalmiNaplo(tmp_path, station=st)
  op = Ft8AutoOperator(station=st, naplo=naplo, tx=Ft8TxPlayer(simulate=True))
  op.set_band("40m", 7.074)
  op.set_armed(True)
  op.on_decode(_report("CQ IK4LZH JN54", snr=-8))
  assert op.phase == QsoPhase.ACTIVE
  assert op._active is not None
  assert op._active.remote_call == "IK4LZH"


def test_strong_fast_cq_answer_skips_grid(tmp_path) -> None:
  from cw_discover.ft8.pro_operator import PriorityMode, ProOperatorConfig

  pro = ProOperatorConfig(enabled=True, priority=PriorityMode.STRONG_FAST, defer_cq_pick=False)
  st = StationIdentity(callsign="N0CALL", grid="JN96", cq_min_snr=-20, ptt_port="", pro=pro)
  op = Ft8AutoOperator(
    station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True)
  )
  op.set_armed(True)
  op.on_decode(_report("CQ IK4LZH JN54", snr=-8, hz=397))
  assert op._last_tx_msg == "IK4LZH N0CALL -08"
  assert "JN96" not in op._last_tx_msg


def test_strong_fast_incoming_grid_skips_our_grid(tmp_path) -> None:
  from cw_discover.ft8.pro_operator import PriorityMode, ProOperatorConfig

  pro = ProOperatorConfig(enabled=True, priority=PriorityMode.STRONG_FAST, defer_cq_pick=False)
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="", pro=pro)
  op = Ft8AutoOperator(
    station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True)
  )
  op.set_armed(True)
  op.on_decode(_report("IK4LZH N0CALL JN54", snr=-10, hz=397))
  assert op._last_tx_msg == "IK4LZH N0CALL -10"
  assert "JN96" not in op._last_tx_msg


def test_incoming_call(tmp_path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
  op = Ft8AutoOperator(station=st, naplo=naplo, tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op.on_decode(_report("IK4LZH N0CALL JN54"))
  assert op._active is not None
  assert op._active.remote_call == "IK4LZH"


def test_no_double_tx_cq_flush_at_tx_period(tmp_path) -> None:
  """PRO defer CQ flush + on_cycle TX periódusban — csak egy adás."""
  from unittest.mock import patch

  from cw_discover.ft8.pro_operator import ProOperatorConfig
  from cw_discover.ft8.sim_harness import Ft8SimHarness

  pro = ProOperatorConfig(enabled=True, defer_cq_pick=True, min_snr=-20, max_snr=10)
  h = Ft8SimHarness(tmp_dir=tmp_path, pro=pro)
  cyc = h.make_cycles(h._fresh_cycle(), 2)[0]
  h.feed("CQ IK4LZH JN54", cycle=cyc, snr=-8, hz=397, wait=False)
  from cw_discover.ft8.ft8_slot import opposite_period, period_from_cycle

  tx_p = opposite_period(period_from_cycle(cyc))
  next_cyc = h.make_cycles(cyc, 2)[1]
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    h.op.on_cycle(next_cyc, time.time())
  h.wait_tx(2, timeout=0.5)
  assert len(h.tx.messages()) == 1
  assert h.tx.messages()[0] == "IK4LZH N0CALL JN96"


def test_report_supersedes_retry_same_period(tmp_path) -> None:
  """Slot eleji retry + partner report ugyanabban a periódusban → csak RR73."""
  from unittest.mock import patch

  from cw_discover.ft8.sim_harness import Ft8SimHarness

  h = Ft8SimHarness(tmp_dir=tmp_path)
  c = h.make_cycles(h._fresh_cycle(), 4)
  h.feed("CQ IK4LZH JN54", cycle=c[0], hz=397)
  tx_p = h.op._active.tx_period
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    h.op.on_cycle(c[1], time.time())
  assert len(h.tx.messages()) == 1
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    h.op.on_cycle(c[2], time.time())
    h.feed("IK4LZH N0CALL R-15", cycle=c[2], hz=397, wait=False)
  h.wait_tx(3, timeout=0.5)
  assert len(h.tx.messages()) == 2
  assert h.tx.messages()[-1] == "IK4LZH N0CALL RR73"


def test_active_report_reversed_call_order(tmp_path) -> None:
  """Élő napló: N0CALL R3HX/P +05 — partner report fordított call sorrend."""
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  cyc = _fresh_cycle(0)
  op.on_decode(_report("CQ R3HX/P KO83", snr=-11, hz=847, cycle=cyc))
  assert op._active.remote_call == "R3HX/P"
  assert op._last_tx_msg == "R3HX/P N0CALL JN96"
  op.on_decode(_report("N0CALL R3HX/P +05", snr=-8, hz=847, cycle=cyc))
  assert op._active.rst_rcvd == "+05"
  assert op._last_tx_msg == "R3HX/P N0CALL R-08"


def test_stale_cq_buffer_not_answered(tmp_path) -> None:
  """Régi CQ a bufferben — flush nem indít QSO-t."""
  from unittest.mock import patch

  from cw_discover.ft8.pro_operator import CqCandidate, ProOperatorConfig
  from cw_discover.ft8.sim_harness import Ft8SimHarness

  h = Ft8SimHarness(tmp_dir=tmp_path, pro=ProOperatorConfig(enabled=True, defer_cq_pick=True))
  h.op._cq_buffer.append(
    CqCandidate(
      call="IK4LZH",
      grid="JN54",
      audio_hz=397,
      snr=-8,
      distance_km=None,
      score=100,
      message="CQ IK4LZH JN54",
      reason="teszt",
      cycle="260704_120000",
    )
  )
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
    h.op._cq_tx_period = 0
    h.op._cq_wait_remaining = 5
    h.op.on_cycle("260704_145100", time.time())
  h.wait_tx(1, timeout=0.3)
  assert h.op._active is None
  assert len(h.tx.messages()) == 0


def test_rr73_cancels_inflight_retry(tmp_path) -> None:
  """RR73 drain érvényteleníti a TX workerben már kivett retry-t (slot várakozás közben)."""
  import threading
  import time

  from cw_discover.ft8.tx_player import TxResult

  class SlowTx:
    def __init__(self) -> None:
      self.messages: list[str] = []

    def transmit(self, message: str, audio_hz: float, *, tx_period=None, should_abort=None):
      time.sleep(0.12)
      if should_abort is not None and should_abort():
        return TxResult(message=message, audio_hz=audio_hz, ok=False, error="cancelled")
      self.messages.append(message)
      return TxResult(message=message, audio_hz=audio_hz, ok=True)

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  slow = SlowTx()
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=slow)
  op.set_armed(True)
  c1 = _fresh_cycle(align_offset=15)
  op._begin_qso("R3HX/P", "KO83", 850.0, heard_period=0)
  op._last_tx_msg = "R3HX/P N0CALL R-07"
  op._queue_tx("R3HX/P N0CALL R-07", 850.0, is_retry=True)

  def rr73() -> None:
    time.sleep(0.05)
    op.on_decode(_report("R3HX/P N0CALL RR73", cycle=c1))

  threading.Thread(target=rr73, daemon=True).start()
  time.sleep(0.6)
  assert "R3HX/P N0CALL R-07" not in slow.messages
  assert slow.messages[-1] == "R3HX/P N0CALL 73"


def test_rr73_then_cycle_no_retry_tx(tmp_path) -> None:
  """RR73 után on_cycle nem küld felesleges retry-t."""
  from unittest.mock import patch

  from cw_discover.ft8.sim_harness import Ft8SimHarness

  h = Ft8SimHarness(tmp_dir=tmp_path)
  c = h.make_cycles(h._fresh_cycle(), 5)
  h.feed("CQ IK4LZH JN54", cycle=c[0], hz=397)
  h.feed("IK4LZH N0CALL -09", cycle=c[1])
  assert h.tx.messages()[-1].endswith("R-10")
  h.feed("IK4LZH N0CALL RR73", cycle=c[2], wait=False)
  before_cycle = h.tx.messages()
  tx_p = h.op._active.tx_period if h.op._active else 0
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    h.op.on_cycle(c[3], time.time())
  h.wait_tx(len(before_cycle) + 1, timeout=0.5)
  after_cycle = h.tx.messages()
  assert after_cycle[-1] == "IK4LZH N0CALL 73"
  assert not any(m.endswith("R-10") for m in after_cycle[len(before_cycle) :])


def test_cq_uzem_ignores_foreign_cq(tmp_path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op.set_cq_only_mode(True)
  op.on_decode(_report("CQ IK4LZH JN54", snr=-8))
  assert op._active is None
  assert op._last_tx_msg == ""


def test_cq_uzem_incoming_report_skip_grid(tmp_path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op.set_cq_only_mode(True)
  op.on_decode(_report("IK4LZH N0CALL -09", snr=-9, hz=397))
  assert op._last_tx_msg == "IK4LZH N0CALL R-09"


def test_cq_uzem_incoming_grid_buffers_then_report(tmp_path) -> None:
  from unittest.mock import patch

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op.set_cq_only_mode(True)
  c = _fresh_cycle(align_offset=15)
  op.on_decode(_report("IK4LZH N0CALL JN54", snr=-10, hz=397, cycle=c))
  assert op._active is None
  assert len(op._incoming_buffer) == 1
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
    op.on_cycle(_fresh_cycle(), time.time())
  assert op._last_tx_msg == "IK4LZH N0CALL -10"
  assert "JN96" not in op._last_tx_msg


def test_reversed_report_idle_still_ignored(tmp_path) -> None:
  """Idle, nem CQ üzem: N0CALL DK7ZT -09 továbbra is spill, nem QSO."""
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op.on_decode(_report("N0CALL DK7ZT -09"))
  assert op._active is None
  assert op._last_tx_msg == ""


def test_reversed_report_idle_cq_uzem_answers(tmp_path) -> None:
  """Idle + CQ üzem: fordított report QSO-k között is válaszol."""
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_cq_only_mode(True)
  op.set_armed(True)
  op._phase = QsoPhase.IDLE
  op._last_tx_msg = ""
  op.on_decode(_report("N0CALL IU4DTV -09", snr=-10, hz=1129))
  assert op._active is not None
  assert op._active.remote_call == "IU4DTV"
  assert op._last_tx_msg == "IU4DTV N0CALL R-10"


def test_qso_log_grid_from_static_cache(tmp_path) -> None:
  """IZ8PPI — grid üzenet nélkül, call_grid_cache.json-ból."""
  import json

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
  op = Ft8AutoOperator(station=st, naplo=naplo, tx=Ft8TxPlayer(simulate=True))
  op.set_cq_only_mode(True)
  op.set_armed(True)
  op._phase = QsoPhase.CALLING_CQ
  op.on_decode(_report("N0CALL IZ8PPI -01", snr=-8, hz=1989))
  assert op._active is not None
  op.on_decode(_report("IZ8PPI N0CALL RR73", snr=-8, hz=1989))
  op.on_decode(_report("IZ8PPI N0CALL 73", snr=-8, hz=1989))
  lines = (tmp_path / "qso.jsonl").read_text(encoding="utf-8").strip().splitlines()
  assert lines
  rec = json.loads(lines[-1])
  assert rec["call"] == "IZ8PPI"
  assert rec["grid"] == "JN81"
  assert rec["grid_source"] == "cache"


def test_active_exchange_ignores_self_echo(tmp_path) -> None:
  """Saját TX visszhang aktív QSO-ban ne indítson új TX-et."""
  from cw_discover.ft8.tx_player import TxResult

  class CountTx:
    def __init__(self) -> None:
      self.messages: list[str] = []

    def transmit(self, message: str, audio_hz: float, *, tx_period=None, should_abort=None):
      self.messages.append(message)
      return TxResult(message=message, audio_hz=audio_hz, ok=True)

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  tx = CountTx()
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=tx)
  op.set_armed(True)
  op.on_decode(_report("CQ IK4LZH JN54", cycle=_fresh_cycle()))
  n_after_cq = len(tx.messages)
  op._last_tx_msg = "IK4LZH N0CALL JN96"
  op.on_decode(_report("IK4LZH N0CALL JN96", cycle=_fresh_cycle()))
  assert len(tx.messages) == n_after_cq


def test_duplicate_grid_does_not_retx_report(tmp_path) -> None:
  """Dupla grid dekód ne küldjön újra reportot (ne resetelje a retry számlálót)."""
  from unittest.mock import patch

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  c = _fresh_cycle()
  op.on_decode(_report("CQ IK4LZH JN54", cycle=c))
  op.on_decode(_report("IK4LZH N0CALL JN54", cycle=c))
  assert op._active.rst_sent
  op.on_decode(_report("IK4LZH N0CALL JN54", cycle=c))
  tx_p = op._active.tx_period
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    op.on_cycle(_fresh_cycle(), time.time())
  assert op._active.cycles_without_reply == 1


def test_closing_ignores_duplicate_r_report(tmp_path) -> None:
  """CLOSING fázisban dupla R-report ne küldjön újra RR73-at."""
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  c = _fresh_cycle()
  op.on_decode(_report("CQ IK4LZH JN54", cycle=c))
  op.on_decode(_report("IK4LZH N0CALL JN54", cycle=c))
  op.on_decode(_report("IK4LZH N0CALL -09", cycle=c))
  op.on_decode(_report("IK4LZH N0CALL R-05", cycle=c))
  assert op.phase == QsoPhase.CLOSING
  op._active.cycles_without_reply = 2
  op.on_decode(_report("IK4LZH N0CALL R-05", cycle=c))
  assert op._active.cycles_without_reply == 2


def test_snap_ft8_hz() -> None:
  from cw_discover.ft8.tx_player import snap_ft8_hz

  assert snap_ft8_hz(1500.0) == 1500.0
  assert snap_ft8_hz(2246.0) == 2243.75
  assert snap_ft8_hz(206.0) == 206.25


def test_cq_adopts_band_audio_hz(tmp_path) -> None:
  """CQ TX Hz követi a hallott sáv aktivitást — nem fix 1500."""
  from cw_discover.ft8.tx_player import snap_ft8_hz

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op.set_cq_only_mode(True)
  op._phase = QsoPhase.CALLING_CQ
  op.on_decode(_report("CQ SP9JMZ JO90", hz=1875, cycle=_fresh_cycle()))
  assert op._default_tx_hz == snap_ft8_hz(1875)
  op.on_decode(_report("CQ N0CALL JN96", hz=1500, cycle=_fresh_cycle()))
  assert op._default_tx_hz == snap_ft8_hz(1875)


def test_closing_rr73_retries_then_finishes(tmp_path) -> None:
  """RR73 után partner nem küld 73-at — RR73 retry, majd naplózás."""
  import json
  from unittest.mock import patch

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
  op = Ft8AutoOperator(station=st, naplo=naplo, tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op._begin_qso("PE1NPS", "JO22", 2246.0, heard_period=1)
  op._last_tx_msg = "PE1NPS N0CALL RR73"
  op._phase = QsoPhase.CLOSING
  op._active.rst_sent = "-18"
  op._active.rst_rcvd = "+00"
  tx_p = op._active.tx_period
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    for _ in range(op.MAX_RETRY_CYCLES + 1):
      op.on_cycle("260704_171500", time.time())
  assert op._active is None
  assert op.phase == QsoPhase.IDLE
  lines = (tmp_path / "qso.jsonl").read_text(encoding="utf-8").strip().splitlines()
  assert json.loads(lines[-1])["call"] == "PE1NPS"


def test_reversed_report_while_calling_cq_answers(tmp_path) -> None:
  """Élő: IZ8PPI → N0CALL fordított sorrend CQ adás közben (N0CALL IZ8PPI +00)."""
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_cq_only_mode(True)
  op.set_armed(True)
  op._phase = QsoPhase.CALLING_CQ
  op._last_tx_msg = "CQ N0CALL JN96"
  op.on_decode(_report("N0CALL IZ8PPI +00", snr=-11, hz=1989))
  assert op._active is not None
  assert op._active.remote_call == "IZ8PPI"
  assert op._last_tx_msg == "IZ8PPI N0CALL R-11"
  assert op._active.rst_sent == "-11"
  assert op._active.rst_rcvd == "+00"


def test_cq_wait_then_repeat_cq(tmp_path) -> None:
  """CQ → várakozás → új CQ; közben bejövő válasz megelőzi."""
  from unittest.mock import patch

  from cw_discover.ft8.pro_operator import ProOperatorConfig

  pro = ProOperatorConfig(enabled=True, defer_cq_pick=True, min_snr=-20, max_snr=15)
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="", pro=pro, cq_repeat_cycles=3)
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op.set_cq_only_mode(True)
  op.set_armed(True)
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
    op._cq_tx_period = 0
    op.on_cycle("c1", time.time())
  assert op._last_tx_msg.startswith("CQ N0CALL")
  assert op._cq_wait_remaining == 3
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
    op.on_cycle("c2", time.time())
  assert op._cq_wait_remaining == 2
  op.on_decode(_report("IK4LZH N0CALL JN54", snr=-8, hz=397, cycle="c3"))
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
    op.on_cycle("c3", time.time())
  assert op._active is not None
  assert op._active.remote_call == "IK4LZH"


def test_reversed_grid_while_calling_cq_cq_uzem(tmp_path) -> None:
  """Fordított grid válasz CQ üzemben — buffer + flush."""
  from unittest.mock import patch

  from cw_discover.ft8.pro_operator import ProOperatorConfig
  from cw_discover.ft8.ft8_slot import opposite_period, period_from_cycle

  pro = ProOperatorConfig(enabled=True, defer_cq_pick=True, min_snr=-20, max_snr=15)
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="", pro=pro)
  naplo = ForgalmiNaplo(tmp_path, station=st)
  op = Ft8AutoOperator(station=st, naplo=naplo, tx=Ft8TxPlayer(simulate=True))
  op.set_cq_only_mode(True)
  op.set_armed(True)
  op._phase = QsoPhase.CALLING_CQ
  op._last_tx_msg = "CQ N0CALL JN96"
  cyc = _fresh_cycle()
  op.on_decode(_report("N0CALL IZ8PPI JN81", snr=-10, hz=1989, cycle=cyc))
  assert op._active is None
  assert len(op._incoming_buffer) == 1
  tx_p = opposite_period(period_from_cycle(cyc))
  next_cyc = time.strftime("%y%m%d_%H%M%S", time.gmtime(int(time.time()) - int(time.time()) % 15 + 15))
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    op.on_cycle(next_cyc, time.time())
  assert op._active is not None
  assert op._active.remote_call == "IZ8PPI"
  assert op._last_tx_msg == "IZ8PPI N0CALL -10"


def test_outbound_fail_cooldown_blocks_cq_not_incoming(tmp_path) -> None:
  """Sikertelen outbound után 10 percig nem CQ-vadász, de bejövő hívás OK."""
  from unittest.mock import patch

  from cw_discover.ft8.sim_harness import Ft8SimHarness

  h = Ft8SimHarness(tmp_dir=tmp_path)
  h.feed("CQ IK4LZH JN54", hz=397)
  tx_p = h.op._active.tx_period
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    for i in range(4):
      h.tick_cycle(f"abandon{i}")
  assert h.phase == QsoPhase.IDLE
  assert h.op._is_outbound_cooldown("IK4LZH")

  h.feed("CQ IK4LZH JN54", hz=397, wait=False)
  assert h.op._active is None
  assert len(h.tx.messages()) == 1

  h.feed("IK4LZH N0CALL JN54", hz=397)
  assert h.op._active is not None
  assert h.op._active.remote_call == "IK4LZH"
  assert len(h.tx.messages()) == 2


def test_outbound_fail_cooldown_expires(tmp_path) -> None:
  """Cooldown lejárta után újra CQ-vadászható."""
  from unittest.mock import patch

  from cw_discover.ft8.sim_harness import Ft8SimHarness

  h = Ft8SimHarness(tmp_dir=tmp_path)
  h.feed("CQ IK4LZH JN54", hz=397)
  tx_p = h.op._active.tx_period
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_p):
    for i in range(4):
      h.tick_cycle(f"abandon{i}")
  t0 = 1000.0
  with patch("cw_discover.ft8.qso_controller.time.monotonic", return_value=t0):
    h.op._mark_outbound_failed("IK4LZH")
    assert h.op._is_outbound_cooldown("IK4LZH")
  with patch(
    "cw_discover.ft8.qso_controller.time.monotonic",
    return_value=t0 + h.op.OUTBOUND_FAIL_COOLDOWN_SEC + 1,
  ):
    assert not h.op._is_outbound_cooldown("IK4LZH")
    h.feed("CQ IK4LZH JN54", hz=397)
    assert h.op._active.remote_call == "IK4LZH"
