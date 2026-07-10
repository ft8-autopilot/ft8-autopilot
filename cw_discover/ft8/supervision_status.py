"""Felügyeleti scriptek — gui_status / hardware_health értelmezés."""
from __future__ import annotations


def hardware_issues_from_status(st: dict) -> list[str]:
  """hardware_health blokk vagy legacy mezők alapján."""
  if not st:
    return []
  hh = st.get("hardware_health")
  if isinstance(hh, dict):
    raw = hh.get("issues")
    if isinstance(raw, list):
      return [str(x) for x in raw if x]
  issues: list[str] = []
  if not st.get("ptt_serial_ok", True):
    issues.append("esp_serial_down")
  if st.get("esp_lock"):
    issues.append("esp_safety_lock")
  if not st.get("safety_mcu_active", True):
    issues.append("esp_mcu_inactive")
  if st.get("safety_tripped"):
    issues.append("safety_tripped")
  if st.get("line_in_ok") is False:
    issues.append("line_in_low")
  if st.get("line_in_tx_blocked"):
    issues.append("line_in_tx_blocked")
  if st.get("last_tx_error"):
    issues.append("last_tx_error")
  return issues


def format_hardware_issues(st: dict) -> str:
  issues = hardware_issues_from_status(st)
  if not issues:
    return ""
  return "hardware: " + ", ".join(issues)
