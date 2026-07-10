"""Élő GUI állapot snapshot — építés és atomi írás."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from cw_discover.ft8.decode_meta import time_iso_utc
from cw_discover.ft8.hardware_health import assess_hardware_health
from cw_discover.ft8.json_fast import dumps_compact
from cw_discover.paths import FORGALMI_LIVE, GUI_STATUS


@dataclass(frozen=True)
class GuiLiveSnapshot:
  callsign: str
  operator: str
  band: str
  dial_mhz: float
  rx_running: bool
  ptt_armed: bool
  pro_operator: bool
  cq_only_mode: bool
  cq_wait_periods: int
  map_visible: bool
  pro_priority: str
  qso_phase: str
  qso_partner: str
  tx_active: bool
  last_tx_error: str
  ptt_serial_ok: bool
  safety_tripped: bool
  safety_reason: str
  safety_watchdog: bool
  safety_line_guard: bool
  safety_mcu_active: bool
  esp_lock: bool | None
  decode_count: int
  line_in_ok: bool
  line_in_rms: float | None
  line_in_tx_blocked: bool
  last_message: str = ""
  note: str = ""


def snapshot_to_dict(snap: GuiLiveSnapshot) -> dict:
  health = assess_hardware_health(snap)
  return {
    "time_utc": time_iso_utc(time.time()),
    "callsign": snap.callsign,
    "operator": snap.operator,
    "band": snap.band,
    "dial_mhz": snap.dial_mhz,
    "rx_running": snap.rx_running,
    "ptt_armed": snap.ptt_armed,
    "pro_operator": snap.pro_operator,
    "cq_only_mode": snap.cq_only_mode,
    "cq_wait_periods": snap.cq_wait_periods,
    "map_visible": snap.map_visible,
    "pro_priority": snap.pro_priority,
    "qso_phase": snap.qso_phase,
    "qso_partner": snap.qso_partner,
    "tx_active": snap.tx_active,
    "last_tx_error": snap.last_tx_error,
    "ptt_serial_ok": snap.ptt_serial_ok,
    "safety_tripped": snap.safety_tripped,
    "safety_reason": snap.safety_reason,
    "safety_watchdog": snap.safety_watchdog,
    "safety_line_guard": snap.safety_line_guard,
    "safety_mcu_active": snap.safety_mcu_active,
    "esp_lock": snap.esp_lock,
    "decode_count": snap.decode_count,
    "line_in_ok": snap.line_in_ok,
    "line_in_rms": snap.line_in_rms,
    "line_in_tx_blocked": snap.line_in_tx_blocked,
    "last_message": snap.last_message,
    "note": snap.note,
    "hardware_health": health.to_dict(),
  }


class LiveStatusPublisher:
  """Rate-limited gui_status.json író."""

  def __init__(
    self,
    path: Path | None = None,
    *,
    min_interval_sec: float = 0.25,
  ) -> None:
    self._path = path or GUI_STATUS
    self._min_interval = max(0.0, float(min_interval_sec))
    self._last_mono = 0.0
    self._last_phase = ""
    self._last_partner = ""

  def should_publish(self, snap: GuiLiveSnapshot, *, force: bool = False) -> bool:
    if force:
      return True
    important = bool(snap.note) or snap.qso_phase != self._last_phase or snap.qso_partner != self._last_partner
    if important:
      return True
    return time.monotonic() - self._last_mono >= self._min_interval

  def publish(self, snap: GuiLiveSnapshot, *, force: bool = False) -> bool:
    if not self.should_publish(snap, force=force):
      return False
    FORGALMI_LIVE.mkdir(parents=True, exist_ok=True)
    self._path.write_text(dumps_compact(snapshot_to_dict(snap)) + "\n", encoding="utf-8")
    self._last_mono = time.monotonic()
    self._last_phase = snap.qso_phase
    self._last_partner = snap.qso_partner
    return True
