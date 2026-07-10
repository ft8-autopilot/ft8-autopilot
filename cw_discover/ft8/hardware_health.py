"""Hardver / biztonság összesített állapot — naplózás és felügyelet."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from cw_discover.gui.live_status import GuiLiveSnapshot


@dataclass(frozen=True)
class HardwareHealth:
  esp_link_ok: bool
  esp_locked: bool | None
  line_in_ok: bool
  line_in_tx_blocked: bool
  ptt_serial_ok: bool
  safety_tripped: bool
  safety_mcu_active: bool
  tx_ready: bool
  issues: tuple[str, ...]

  def to_dict(self) -> dict:
    return {
      "esp_link_ok": self.esp_link_ok,
      "esp_locked": self.esp_locked,
      "line_in_ok": self.line_in_ok,
      "line_in_tx_blocked": self.line_in_tx_blocked,
      "ptt_serial_ok": self.ptt_serial_ok,
      "safety_tripped": self.safety_tripped,
      "safety_mcu_active": self.safety_mcu_active,
      "tx_ready": self.tx_ready,
      "issues": list(self.issues),
    }


def assess_hardware_health(snap: "GuiLiveSnapshot") -> HardwareHealth:
  issues: list[str] = []
  if not snap.ptt_serial_ok:
    issues.append("esp_serial_down")
  if snap.esp_lock:
    issues.append("esp_safety_lock")
  if not snap.safety_mcu_active:
    issues.append("esp_mcu_inactive")
  if snap.safety_tripped:
    issues.append("safety_tripped")
  if not snap.line_in_ok:
    issues.append("line_in_low")
  if snap.line_in_tx_blocked:
    issues.append("line_in_tx_blocked")
  if snap.last_tx_error:
    issues.append("last_tx_error")

  tx_ready = not issues and not snap.tx_active
  return HardwareHealth(
    esp_link_ok=snap.ptt_serial_ok,
    esp_locked=snap.esp_lock,
    line_in_ok=snap.line_in_ok,
    line_in_tx_blocked=snap.line_in_tx_blocked,
    ptt_serial_ok=snap.ptt_serial_ok,
    safety_tripped=snap.safety_tripped,
    safety_mcu_active=snap.safety_mcu_active,
    tx_ready=tx_ready,
    issues=tuple(issues),
  )
