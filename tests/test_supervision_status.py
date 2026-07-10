from cw_discover.ft8.supervision_status import format_hardware_issues, hardware_issues_from_status


def test_hardware_issues_from_block() -> None:
  st = {
    "hardware_health": {
      "issues": ["esp_serial_down", "last_tx_error"],
      "tx_ready": False,
    }
  }
  assert hardware_issues_from_status(st) == ["esp_serial_down", "last_tx_error"]
  assert "esp_serial_down" in format_hardware_issues(st)


def test_hardware_issues_legacy_fallback() -> None:
  st = {
    "ptt_serial_ok": False,
    "line_in_ok": False,
    "last_tx_error": "PING fail",
  }
  issues = hardware_issues_from_status(st)
  assert "esp_serial_down" in issues
  assert "line_in_low" in issues
  assert "last_tx_error" in issues
