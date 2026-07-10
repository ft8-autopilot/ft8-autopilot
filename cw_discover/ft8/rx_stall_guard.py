"""RX dekód stall felügyelet — hosszú csend észlelése."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RxStallStep:
  stalled: bool
  elapsed_sec: float
  should_report: bool


class RxStallGuard:
  def __init__(self, stall_sec: float = 300.0) -> None:
    self._stall_sec = max(1.0, float(stall_sec))
    self._last_decode_count = 0
    self._since_mono = 0.0
    self._reported = False

  def reset(self, *, decode_count: int, now_mono: float) -> None:
    self._last_decode_count = decode_count
    self._since_mono = now_mono
    self._reported = False

  def observe(self, *, decode_count: int, now_mono: float, rx_running: bool) -> RxStallStep:
    if not rx_running:
      return RxStallStep(stalled=False, elapsed_sec=0.0, should_report=False)

    if decode_count > self._last_decode_count:
      self._last_decode_count = decode_count
      self._since_mono = now_mono
      self._reported = False
      return RxStallStep(stalled=False, elapsed_sec=0.0, should_report=False)

    elapsed = now_mono - self._since_mono
    stalled = elapsed >= self._stall_sec
    should_report = stalled and not self._reported
    if should_report:
      self._reported = True
    return RxStallStep(stalled=stalled, elapsed_sec=elapsed, should_report=should_report)
