"""ESP32 RESUME / LOCK feloldás — tesztelhető üzleti logika."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PttResumeBackend(Protocol):
  last_error: str

  def resume(self) -> bool: ...
  def sync_time(self) -> None: ...
  def ping(self) -> bool: ...


@dataclass(frozen=True)
class EspResumeResult:
  ok: bool
  error: str = ""
  ptt_ok: bool = False


def try_resume_esp(ptt: PttResumeBackend, *, reason: str) -> EspResumeResult:
  if not hasattr(ptt, "resume"):
    return EspResumeResult(ok=False, error=f"Nincs RESUME ({reason})")
  if not ptt.resume():
    err = getattr(ptt, "last_error", "ESP32 RESUME sikertelen")
    return EspResumeResult(ok=False, error=err)
  ptt.sync_time()
  ptt_ok = ptt.ping()
  return EspResumeResult(ok=True, ptt_ok=ptt_ok)
