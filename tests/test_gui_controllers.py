from cw_discover.ft8.esp_resume import EspResumeResult, try_resume_esp
from cw_discover.ft8.line_in_level_tracker import LineInLevelTracker
from cw_discover.ft8.rx_stall_guard import RxStallGuard
from cw_discover.gui.controllers.line_in import LineInEventKind, LineInStateController
from cw_discover.gui.controllers.safety import (
  apply_reactivate_plan,
  esp_trip_reason_unlockable,
  plan_esp_unlock_reactivate,
)
from cw_discover.ft8.hardware_health import assess_hardware_health
from cw_discover.ft8.safety_manager import SafetySnapshot
from cw_discover.gui.controllers.esp_link import EspLinkController, EspLinkEventKind
from cw_discover.gui.controllers.operator_in import (
  OperatorCmdKind,
  parse_operator_batch,
  parse_operator_line,
  parse_priority_mode,
)
from cw_discover.gui.controllers.operator_in_reader import OperatorInReader
from cw_discover.gui.controllers.table_engage import (
  TableEngageReject,
  parse_audio_hz_column,
  parse_table_engage,
  resolve_remote_call,
)
from cw_discover.gui.live_status import GuiLiveSnapshot, LiveStatusPublisher, snapshot_to_dict


def test_esp_controller_disconnect_and_recover_events() -> None:
  ctrl = EspLinkController()

  assert not ctrl.poll(ping_ok=True, tx_active=False, now_mono=1.0)
  events = ctrl.poll(ping_ok=False, tx_active=False, now_mono=2.0, last_error="ttyUSB0 missing")
  assert len(events) == 2
  assert events[0].kind == EspLinkEventKind.DISCONNECTED
  assert "ttyUSB0" in events[0].detail
  assert events[1].kind == EspLinkEventKind.RECOVER_TRY

  restored = ctrl.poll(ping_ok=True, tx_active=False, now_mono=3.0)
  assert len(restored) == 1
  assert restored[0].kind == EspLinkEventKind.RESTORED


def test_rx_stall_guard_reports_once() -> None:
  guard = RxStallGuard(stall_sec=60.0)
  guard.reset(decode_count=0, now_mono=100.0)

  s1 = guard.observe(decode_count=0, now_mono=120.0, rx_running=True)
  assert not s1.should_report

  s2 = guard.observe(decode_count=0, now_mono=161.0, rx_running=True)
  assert s2.should_report

  s3 = guard.observe(decode_count=0, now_mono=200.0, rx_running=True)
  assert not s3.should_report


def test_live_status_publisher_rate_limit() -> None:
  import tempfile
  from pathlib import Path

  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "gui_status.json"
    pub = LiveStatusPublisher(path, min_interval_sec=10.0)
    snap = GuiLiveSnapshot(
      callsign="TEST",
      operator="",
      band="40m",
      dial_mhz=7.074,
      rx_running=True,
      ptt_armed=False,
      pro_operator=False,
      cq_only_mode=False,
      cq_wait_periods=3,
      map_visible=True,
      pro_priority="balanced",
      qso_phase="idle",
      qso_partner="",
      tx_active=False,
      last_tx_error="",
      ptt_serial_ok=True,
      safety_tripped=False,
      safety_reason="",
      safety_watchdog=True,
      safety_line_guard=True,
      safety_mcu_active=True,
      esp_lock=False,
      decode_count=0,
      line_in_ok=True,
      line_in_rms=0.1,
      line_in_tx_blocked=False,
    )
    assert pub.publish(snap)
    assert path.exists()
    assert not pub.publish(snap)
    assert pub.publish(snap, force=True)
    data = snapshot_to_dict(snap)
    assert data["callsign"] == "TEST"


def test_operator_in_parser_band_and_priority() -> None:
  band = parse_operator_line("BAND 40")
  assert band is not None
  assert band.kind == OperatorCmdKind.BAND
  assert band.arg1 == "40m"

  prio = parse_operator_line("PRO_PRIORITY gyenge")
  assert prio is not None
  assert parse_priority_mode(prio.arg1).value == "weak_dx"

  batch = parse_operator_batch("PTT_ON\nDIAL 7.074\n")
  assert [c.kind for c in batch] == [OperatorCmdKind.PTT_ON, OperatorCmdKind.DIAL]


def test_operator_in_reader_consumes_once(tmp_path) -> None:
  path = tmp_path / "operator_in.txt"
  path.write_text("START_RX\n", encoding="utf-8")
  reader = OperatorInReader(path)
  first = reader.consume_if_changed()
  assert first == "START_RX"
  assert path.read_text(encoding="utf-8") == ""
  second = reader.consume_if_changed()
  assert second is None


def test_line_in_state_controller_transitions() -> None:
  ctrl = LineInStateController(min_rms=0.3)
  low = ctrl.on_signal_change(False, 0.1)
  assert low is not None
  assert low.kind == LineInEventKind.LOW
  assert ctrl.low

  again = ctrl.on_signal_change(False, 0.05)
  assert again is None

  restored = ctrl.on_signal_change(True, 0.4)
  assert restored is not None
  assert restored.kind == LineInEventKind.RESTORED
  assert not ctrl.low


def test_line_in_level_tracker_fast_detect() -> None:
  tracker = LineInLevelTracker(min_rms=0.3, fast_samples=3)
  for _ in range(2):
    assert not tracker.observe(0.1, currently_low=False).should_evaluate
  assert tracker.observe(0.1, currently_low=False).should_evaluate


class _FakePtt:
  last_error = ""

  def __init__(self, ok: bool) -> None:
    self._ok = ok

  def resume(self) -> bool:
    return self._ok

  def sync_time(self) -> None:
    pass

  def ping(self) -> bool:
    return self._ok


def test_try_resume_esp_success_and_fail() -> None:
  ok = try_resume_esp(_FakePtt(True), reason="test")
  assert isinstance(ok, EspResumeResult)
  assert ok.ok and ok.ptt_ok

  fail = try_resume_esp(_FakePtt(False), reason="test")
  assert not fail.ok


def test_safety_esp_unlock_plan() -> None:
  snap = SafetySnapshot(tripped=True, reason="ESP LOCK (TX): timeout")
  assert esp_trip_reason_unlockable(snap.reason)
  plan = plan_esp_unlock_reactivate(snap, watchdog=True, line_guard=False)
  assert plan is not None
  apply_reactivate_plan(snap, plan)
  assert not snap.tripped
  assert snap.mcu_active


def test_hardware_health_flags_esp_disconnect() -> None:
  snap = GuiLiveSnapshot(
    callsign="TEST",
    operator="",
    band="40m",
    dial_mhz=7.074,
    rx_running=True,
    ptt_armed=True,
    pro_operator=False,
    cq_only_mode=False,
    cq_wait_periods=3,
    map_visible=True,
    pro_priority="balanced",
    qso_phase="idle",
    qso_partner="",
    tx_active=False,
    last_tx_error="PING nincs PONG",
    ptt_serial_ok=False,
    safety_tripped=False,
    safety_reason="",
    safety_watchdog=True,
    safety_line_guard=True,
    safety_mcu_active=True,
    esp_lock=False,
    decode_count=0,
    line_in_ok=True,
    line_in_rms=0.2,
    line_in_tx_blocked=False,
  )
  health = assess_hardware_health(snap)
  assert not health.tx_ready
  assert "esp_serial_down" in health.issues
  assert "last_tx_error" in health.issues
  d = health.to_dict()
  assert d["esp_link_ok"] is False


def test_table_engage_cq_and_directed() -> None:
  cq = parse_table_engage(
    message="CQ IK4LZH JN54",
    my_callsign="N0CALL",
    audio_hz_text="397 (7074.000 kHz)",
    snr_text="-8",
  )
  assert cq.ok and cq.request is not None
  assert cq.request.call == "IK4LZH"
  assert cq.request.audio_hz == 397.0
  assert cq.request.rx_snr == -8
  assert cq.request.rx_report == ""

  directed = parse_table_engage(
    message="IK4LZH N0CALL -09",
    my_callsign="N0CALL",
    audio_hz_text="1867",
    snr_text="-9",
  )
  assert directed.ok and directed.request is not None
  assert directed.request.call == "IK4LZH"
  assert directed.request.rx_report == "-09"
  assert directed.request.rx_snr == -9


def test_table_engage_rejects_invalid() -> None:
  bad = parse_table_engage(
    message="CQ IK4LZH JN54",
    my_callsign="N0CALL",
    audio_hz_text="",
    snr_text="0",
  )
  assert not bad.ok
  assert bad.reject == TableEngageReject.INVALID_HZ

  assert resolve_remote_call("CQ IK4LZH JN54", "N0CALL") == "IK4LZH"
  assert parse_audio_hz_column("397 (7074.000 kHz)") == 397.0
