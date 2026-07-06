"""Jövőben kritikus, korábban kimaradt FT8 tesztek."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cw_discover.ft8.atomic_io import AtomicJsonlSink
from cw_discover.ft8.engine import DecodeReport
from cw_discover.ft8.forgalmi_log import ForgalmiNaplo, QsoRecord, record_to_adif_line
from cw_discover.ft8.ft8_slot import decode_is_fresh
from cw_discover.ft8.log_replay import fresh_base_cycle, cycles_from_base
from cw_discover.ft8.pro_operator import ProOperatorConfig, score_cq_candidate
from cw_discover.ft8.qso_controller import Ft8AutoOperator, QsoPhase
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.ft8.ft8_protocol import message_triplet
from cw_discover.ft8.engine import DecodeReport as DR
from cw_discover.ft8.sim_harness import Ft8SimHarness, RecordingTx
from cw_discover.ft8.tx_player import Ft8TxPlayer


def _h(tmp_path) -> Ft8SimHarness:
  return Ft8SimHarness(tmp_dir=tmp_path)


def _cyc(n: int = 6) -> list[str]:
  return cycles_from_base(fresh_base_cycle(), n)


# --- 1–2: QSO után echo / dupla log ---


def test_post_qso_rr73_echo_no_new_qso(tmp_path) -> None:
  """Lezárt QSO után RR73 echo — ne induljon új QSO."""
  h = _h(tmp_path)
  c = _cyc(5)
  h.feed("CQ IK4LZH JN54", cycle=c[0], hz=397)
  h.feed("IK4LZH N0CALL -09", cycle=c[1])
  h.feed("IK4LZH N0CALL R-05", cycle=c[2])
  h.feed("IK4LZH N0CALL RR73", cycle=c[3])
  assert h.phase == QsoPhase.IDLE
  n_before = len(h.tx.messages())
  h.feed("IK4LZH N0CALL RR73", cycle=c[4], wait=False)
  assert h.phase == QsoPhase.IDLE
  assert h.op._active is None
  assert len(h.tx.messages()) == n_before


def test_post_qso_no_double_log(tmp_path) -> None:
  h = _h(tmp_path)
  c = _cyc(5)
  for msg, ci in zip(
    ["CQ IK4LZH JN54", "IK4LZH N0CALL -09", "IK4LZH N0CALL R-05", "IK4LZH N0CALL RR73"],
    c[:4],
  ):
    h.feed(msg, cycle=ci)
  lines = (tmp_path / "qso.jsonl").read_text().strip().splitlines()
  assert len(lines) == 1
  h.feed("IK4LZH N0CALL 73", cycle=c[4], wait=False)
  lines2 = (tmp_path / "qso.jsonl").read_text().strip().splitlines()
  assert len(lines2) == 1


# --- 3: engage aktív QSO alatt ---


def test_engage_call_overwrites_active(tmp_path) -> None:
  h = _h(tmp_path)
  h.feed("CQ IK4LZH JN54", hz=397)
  h.op.engage_call("DK7ZT", 1867.0)
  h.wait_tx(2)
  assert h.op._active.remote_call == "DK7ZT"


# --- 4–5: szálak + shutdown ---


def test_concurrent_decode_and_cycle(tmp_path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
  tx = RecordingTx()
  op = Ft8AutoOperator(station=st, naplo=naplo, tx=tx)
  op.set_armed(True)
  err: list[Exception] = []

  def worker_decode():
    try:
      for _ in range(30):
        op.on_decode(
          DR(
            cycle=fresh_base_cycle(),
            snr=-10,
            dt=0.1,
            audio_hz=1500,
            rf_khz=7074.0,
            message="CQ IK4LZH JN54",
            time_received=time.time(),
          )
        )
    except Exception as e:
      err.append(e)

  def worker_cycle():
    try:
      for i in range(30):
        op.on_cycle(f"c{i}", time.time())
    except Exception as e:
      err.append(e)

  t1 = threading.Thread(target=worker_decode)
  t2 = threading.Thread(target=worker_cycle)
  t1.start()
  t2.start()
  t1.join(timeout=5)
  t2.join(timeout=5)
  assert not err


def test_shutdown_tx_worker(tmp_path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=RecordingTx())
  op.set_armed(True)
  op.shutdown()
  op._worker.join(timeout=2)
  assert not op._worker.is_alive()


def test_esp32_ptt_close_no_deadlock() -> None:
  """close() korábban _cmd()-ot hívott lock alatt → örök deadlock ha _ser nyitva."""
  from unittest.mock import MagicMock

  from cw_discover.ft8.ptt_client import Esp32Ptt

  ptt = Esp32Ptt("/dev/ttyTEST")
  mock_ser = MagicMock()
  mock_ser.in_waiting = 0
  ptt._ser = mock_ser
  err: list[BaseException] = []

  def work() -> None:
    try:
      ptt.close()
    except BaseException as exc:
      err.append(exc)

  t = threading.Thread(target=work, daemon=True)
  t.start()
  t.join(timeout=1.0)
  assert not t.is_alive(), "Esp32Ptt.close() deadlock"
  assert not err
  assert ptt._ser is None


# --- 6: PTT hiba ---


def test_ptt_fail_not_ok() -> None:
  ptt = MagicMock()
  ptt.ptt_on.return_value = False
  ptt.last_error = "mock_fail"
  player = Ft8TxPlayer(ptt=ptt, simulate=False)
  r = player.transmit("CQ N0CALL JN96", 1500.0, tx_period=0)
  assert not r.ok
  assert "mock" in r.error.lower() or r.error


# --- 7: Hz lock ---


def test_hz_not_follow_remote(tmp_path) -> None:
  h = _h(tmp_path)
  c = _cyc(4)
  h.feed("CQ IK4LZH JN54", cycle=c[0], hz=397)
  h.feed("IK4LZH N0CALL -09", cycle=c[1], hz=2200)
  h.feed("IK4LZH N0CALL R-05", cycle=c[2], hz=999)
  assert all(tx.audio_hz == 397 for tx in h.tx.calls)


# --- 8–9: napló ---


def test_worked_cache_skips_cq(tmp_path) -> None:
  naplo = ForgalmiNaplo(tmp_path, station=StationIdentity(callsign="N0CALL", grid="JN96", ptt_port=""))
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
      tx_audio_hz=397,
    )
  )
  naplo2 = ForgalmiNaplo(tmp_path, station=StationIdentity(callsign="N0CALL", grid="JN96", ptt_port=""))
  assert naplo2.recently_worked("IK4LZH", band="40m")
  h = Ft8SimHarness(tmp_dir=tmp_path)
  h.naplo = naplo2
  h.op.naplo = naplo2
  h.feed("CQ IK4LZH JN54", snr=-8, wait=False)
  assert h.op._active is None


def test_adif_line_has_call_and_eor(tmp_path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  rec = QsoRecord(
    call="IK4LZH",
    grid="JN54",
    band="40m",
    dial_mhz=7.074,
    rst_sent="-10",
    rst_rcvd="-09",
    time_on=datetime.now(tz=timezone.utc),
    time_off=datetime.now(tz=timezone.utc),
  )
  line = record_to_adif_line(rec, st)
  assert "<call:6>IK4LZH" in line
  assert "<eor>" in line


# --- 10: atomic io ---


def test_atomic_jsonl_survives_read(tmp_path) -> None:
  p = tmp_path / "qso.jsonl"
  sink = AtomicJsonlSink(p, power_safe=True)
  sink.append({"call": "TEST", "band": "40m"})
  sink.append({"call": "TEST2", "band": "20m"})
  lines = p.read_text().strip().splitlines()
  assert len(lines) == 2
  assert "TEST" in lines[0]


# --- 11–12: PRO scoring ---


def test_pro_distance_prefers_farther(tmp_path) -> None:
  from cw_discover.ft8.pro_operator import pick_best_cq, CqCandidate

  near = CqCandidate("NEAR", "JN96", 1500, -10, 50.0, 50.0, "CQ NEAR", "near", "")
  far = CqCandidate("FAR", "JM77", 1500, -12, 2500.0, 2500.0, "CQ FAR", "far", "")
  best = pick_best_cq([near, far])
  assert best is not None
  assert best.call == "FAR"


def test_pro_min_distance_filters(tmp_path) -> None:
  cfg = ProOperatorConfig(enabled=True, min_distance_km=500)
  tri = message_triplet("CQ NEAR JN96")
  assert tri is not None
  r = DR(
    cycle=fresh_base_cycle(),
    snr=-10,
    dt=0.1,
    audio_hz=1500,
    rf_khz=7074.0,
    message="CQ NEAR JN96",
    time_received=time.time(),
  )
  cand = score_cq_candidate(
    report=r,
    triplet=tri,
    grid="JN96",
    distance_km=100.0,
    worked=False,
    config=cfg,
  )
  assert cand is None


# --- 17: future cycle ---


def test_future_cycle_still_fresh() -> None:
  import calendar

  t = int(time.time()) + 30
  t -= t % 15
  cycle = time.strftime("%y%m%d_%H%M%S", time.gmtime(t))
  assert decode_is_fresh(cycle)


def test_worked_per_band_not_other_band(tmp_path) -> None:
  """WSJT-X dupe: call+band — más sáv még szabad."""
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
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
    )
  )
  assert naplo.recently_worked("IK4LZH", band="40m")
  assert not naplo.recently_worked("IK4LZH", band="20m")


def test_worked_yesterday_not_today(tmp_path) -> None:
  """UTC fordulónap után újra hívható."""
  from datetime import timedelta

  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
  yesterday = datetime.now(tz=timezone.utc) - timedelta(days=1)
  naplo.append_qso(
    QsoRecord(
      call="IK4LZH",
      grid="JN54",
      band="40m",
      dial_mhz=7.074,
      rst_sent="-10",
      rst_rcvd="-09",
      time_on=yesterday,
      time_off=yesterday,
    )
  )
  assert not naplo.recently_worked("IK4LZH", band="40m")


# --- TX encode ---


def test_build_wave_valid_message() -> None:
  p = Ft8TxPlayer(simulate=True)
  w = p.build_wave("CQ N0CALL JN96", 1500.0)
  assert w is not None
  assert len(w) > 1000


def test_build_wave_invalid_too_short() -> None:
  p = Ft8TxPlayer(simulate=True)
  assert p.build_wave("CQ N0CALL", 1500.0) is None
