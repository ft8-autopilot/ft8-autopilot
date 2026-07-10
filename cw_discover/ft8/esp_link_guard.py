"""ESP32 USB/soros kapcsolat őr állapotgép."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EspLinkStep:
  """Egy poll ciklus döntése."""

  link_down: bool
  just_went_down: bool
  just_restored: bool
  should_try_recover: bool


class EspLinkGuard:
  """TX-en kívüli ESP link felügyelet és retry ütemezés."""

  def __init__(self, retry_sec: float = 2.0) -> None:
    self._retry_sec = max(0.1, float(retry_sec))
    self._link_down = False
    self._last_recover_try_mono = 0.0

  @property
  def link_down(self) -> bool:
    return self._link_down

  def mark_restored(self) -> None:
    self._link_down = False

  def observe(self, *, ping_ok: bool, tx_active: bool, now_mono: float) -> EspLinkStep:
    if tx_active:
      return EspLinkStep(
        link_down=self._link_down,
        just_went_down=False,
        just_restored=False,
        should_try_recover=False,
      )

    if ping_ok:
      just_restored = self._link_down
      self._link_down = False
      return EspLinkStep(
        link_down=False,
        just_went_down=False,
        just_restored=just_restored,
        should_try_recover=False,
      )

    just_went_down = not self._link_down
    self._link_down = True
    should_try = now_mono - self._last_recover_try_mono >= self._retry_sec
    if should_try:
      self._last_recover_try_mono = now_mono
    return EspLinkStep(
      link_down=True,
      just_went_down=just_went_down,
      just_restored=False,
      should_try_recover=should_try,
    )
