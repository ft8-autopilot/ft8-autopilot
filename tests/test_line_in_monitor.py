"""Line-in monitor és TX tiltás tesztek."""
from __future__ import annotations

import threading
import time

import pytest

from cw_discover.ft8.line_in_monitor import LINE_IN_MIN_RMS, LineInMonitor
from cw_discover.ft8.tx_player import Ft8TxPlayer


def test_line_in_monitor_detects_low_and_recovery() -> None:
  events: list[tuple[bool, float]] = []
  mon = LineInMonitor(
    "mock.pulse",
    on_change=lambda ok, rms: events.append((ok, rms)),
    measure=lambda _p, _d: 0.05,
  )
  ok, rms = mon.check_once()
  assert ok is False
  assert rms == pytest.approx(0.05)
  assert events == [(False, pytest.approx(0.05))]

  mon2 = LineInMonitor("mock.pulse", on_change=lambda ok, rms: events.append((ok, rms)))
  mon2._apply_state(False, 0.05)
  mon2._apply_state(True, 0.85)
  assert events[-2:] == [(False, 0.05), (True, 0.85)]
  assert mon2.tx_allowed() is True


def test_line_in_monitor_thread_loop() -> None:
  values = [0.9, 0.04, 0.9]
  lock = threading.Lock()

  def measure(_pulse: str, _dur: float) -> float:
    with lock:
      return values.pop(0) if values else 0.9

  changes: list[bool] = []
  mon = LineInMonitor(
    "mock.pulse",
    on_change=lambda ok, _rms: changes.append(ok),
    measure=measure,
    interval_s=0.08,
  )
  mon.start()
  time.sleep(0.35)
  mon.stop()
  assert False in changes
  assert changes[-1] is True


def test_tx_player_blocks_when_line_in_low() -> None:
  tx = Ft8TxPlayer(simulate=True, line_in_guard=lambda: False)
  r = tx.transmit("CQ N0CALL JN96", 1500.0)
  assert not r.ok
  assert r.error == "line_in_blocked"


def test_tx_player_allows_when_line_in_ok() -> None:
  tx = Ft8TxPlayer(simulate=True, line_in_guard=lambda: True)
  r = tx.transmit("CQ N0CALL JN96", 1500.0)
  assert r.ok


def test_line_in_monitor_rms_provider() -> None:
  provider = iter([0.9, 0.02, 0.88])
  mon = LineInMonitor("mock.pulse", rms_provider=lambda: next(provider))
  ok, rms = mon.check_once()
  assert ok and rms == 0.9
  ok, rms = mon.check_once()
  assert not ok and rms == 0.02
