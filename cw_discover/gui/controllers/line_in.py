"""Line-in állapot események — GUI/napló réteg."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class LineInEventKind(Enum):
  LOW = auto()
  RESTORED = auto()


@dataclass(frozen=True)
class LineInEvent:
  kind: LineInEventKind
  rms: float
  min_rms: float
  log_code: str
  detail: str = ""


class LineInStateController:
  def __init__(self, *, min_rms: float) -> None:
    self._min_rms = min_rms
    self._low = False

  @property
  def low(self) -> bool:
    return self._low

  def on_signal_change(self, ok: bool, rms: float) -> LineInEvent | None:
    was_low = self._low
    self._low = not ok
    if ok:
      if was_low:
        return LineInEvent(
          kind=LineInEventKind.RESTORED,
          rms=rms,
          min_rms=self._min_rms,
          log_code="rx_linein_restored",
          detail=f"RMS {rms:.4f}",
        )
      return None
    if not was_low:
      return LineInEvent(
        kind=LineInEventKind.LOW,
        rms=rms,
        min_rms=self._min_rms,
        log_code="rx_linein_low",
        detail=f"RMS {rms:.4f} < {self._min_rms}",
      )
    return None

  def reset(self) -> None:
    self._low = False
