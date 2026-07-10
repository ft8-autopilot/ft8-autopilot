"""ESP32 USB/soros link felügyelet — esemény-alapú vezérlő."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from cw_discover.ft8.esp_link_guard import EspLinkGuard


class EspLinkEventKind(Enum):
  RESTORED = auto()
  DISCONNECTED = auto()
  RECOVER_TRY = auto()
  AUTO_RECOVERED = auto()


@dataclass(frozen=True)
class EspLinkEvent:
  kind: EspLinkEventKind
  detail: str = ""
  status_note: str = ""
  log_codes: tuple[str, ...] = ()


class EspLinkController:
  """Ping + guard állapotgép → GUI/napló események."""

  def __init__(self, guard: EspLinkGuard | None = None) -> None:
    self._guard = guard or EspLinkGuard(retry_sec=2.0)

  @property
  def link_down(self) -> bool:
    return self._guard.link_down

  def mark_restored(self) -> None:
    self._guard.mark_restored()

  def poll(
    self,
    *,
    ping_ok: bool,
    tx_active: bool,
    now_mono: float,
    last_error: str = "",
  ) -> list[EspLinkEvent]:
    step = self._guard.observe(ping_ok=ping_ok, tx_active=tx_active, now_mono=now_mono)
    err = (last_error or "ESP32 nem válaszol").strip()
    events: list[EspLinkEvent] = []

    if step.just_restored:
      events.append(
        EspLinkEvent(
          kind=EspLinkEventKind.RESTORED,
          detail="PING újra OK",
          status_note="esp_link_restored",
          log_codes=("esp_usb_recovered",),
        )
      )
      return events

    if step.just_went_down:
      events.append(
        EspLinkEvent(
          kind=EspLinkEventKind.DISCONNECTED,
          detail=err,
          status_note=f"esp_link_down:{err}",
          log_codes=("esp_usb_disconnected", "esp_usb_serial"),
        )
      )

    if step.should_try_recover and step.link_down:
      events.append(
        EspLinkEvent(
          kind=EspLinkEventKind.RECOVER_TRY,
          detail=err,
          log_codes=("esp_usb_recover_try",),
        )
      )

    return events

  def on_recover_success(self) -> EspLinkEvent:
    return EspLinkEvent(
      kind=EspLinkEventKind.AUTO_RECOVERED,
      detail="auto-reconnect OK",
      status_note="esp_auto_recovered",
      log_codes=("esp_usb_recovered",),
    )
