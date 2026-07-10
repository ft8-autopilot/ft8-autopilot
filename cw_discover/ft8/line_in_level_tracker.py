"""Line-in gyors szintkövetés — streak alapú észlelés a monitor előtt."""
from __future__ import annotations

from dataclasses import dataclass

from cw_discover.ft8.line_in_monitor import LINE_IN_MIN_RMS


@dataclass(frozen=True)
class LineInLevelStep:
  should_evaluate: bool
  low_streak: int
  high_streak: int


class LineInLevelTracker:
  """Gyors RMS minták alapján dönt, mikor hívjuk a LineInMonitor.evaluate-t."""

  def __init__(
    self,
    *,
    min_rms: float = LINE_IN_MIN_RMS,
    fast_samples: int = 8,
  ) -> None:
    self._min_rms = min_rms
    self._fast_samples = max(1, int(fast_samples))
    self._low_streak = 0
    self._high_streak = 0

  def reset(self) -> None:
    self._low_streak = 0
    self._high_streak = 0

  def observe(self, raw_rms: float, *, currently_low: bool) -> LineInLevelStep:
    low = raw_rms < self._min_rms
    if low:
      self._low_streak += 1
      self._high_streak = 0
      should = (not currently_low) and self._low_streak >= self._fast_samples
      return LineInLevelStep(should_evaluate=should, low_streak=self._low_streak, high_streak=0)

    self._high_streak += 1
    self._low_streak = 0
    should = currently_low and self._high_streak >= self._fast_samples
    return LineInLevelStep(
      should_evaluate=should,
      low_streak=0,
      high_streak=self._high_streak,
    )
