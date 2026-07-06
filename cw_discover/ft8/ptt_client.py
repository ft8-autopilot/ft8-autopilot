"""ESP32 soros PTT — FT8 slot ütemezés."""
from __future__ import annotations

import threading
import time
from typing import Protocol


class PttBackend(Protocol):
  def ptt_on(self) -> bool: ...

  def ptt_off(self) -> bool: ...

  def sync_time(self) -> None: ...


class NullPtt:
  """Teszt / szimuláció — nem kulcsol rádiót."""

  last_error: str = ""

  def ptt_on(self) -> bool:
    return True

  def ptt_off(self) -> bool:
    return True

  def sync_time(self) -> None:
    pass

  def shutdown(self) -> bool:
    return True

  def resume(self) -> bool:
    return True


class Esp32Ptt:
  def __init__(self, port: str = "/dev/ttyUSB0", baud: int = 115200) -> None:
    self.port = port
    self.baud = baud
    self._lock = threading.Lock()
    self._ser = None
    self.last_error = ""

  def _open(self):
    if self._ser is not None:
      return self._ser
    import serial

    self._ser = serial.Serial(self.port, self.baud, timeout=0.5, write_timeout=1.0)
    time.sleep(0.15)
    return self._ser

  def _cmd_unlocked(self, line: str, wait: float = 0.25) -> list[str]:
    """Soros parancs — a hívó már tartja a self._lock-ot (close/shutdown)."""
    try:
      ser = self._open()
      ser.reset_input_buffer()
      ser.write((line.strip() + "\n").encode())
      ser.flush()
      time.sleep(wait)
      out: list[str] = []
      while ser.in_waiting:
        out.append(ser.readline().decode(errors="replace").strip())
      for ln in out:
        if "WARN PTT_STUCK" in ln:
          self.last_error = ln
      return out
    except Exception as exc:
      self.last_error = f"{line}: {exc}"
      return []

  def _cmd(self, line: str, wait: float = 0.25) -> list[str]:
    with self._lock:
      return self._cmd_unlocked(line, wait)

  def ping(self) -> bool:
    lines = self._cmd("PING")
    ok = any("PONG" in ln for ln in lines)
    if not ok:
      self.last_error = f"PING nincs PONG: {lines!r}"
    return ok

  def status(self) -> dict[str, int | bool]:
    """ESP STATUS → TIME, PTT, LOCK."""
    out: dict[str, int | bool] = {"ptt": 0, "lock": False}
    for ln in self._cmd("STATUS", wait=0.35):
      if "LOCK=" in ln:
        try:
          out["lock"] = ln.split("LOCK=")[1].strip().startswith("1")
        except IndexError:
          pass
      if "PTT=" in ln:
        try:
          out["ptt"] = int(ln.split("PTT=")[1].split()[0])
        except (IndexError, ValueError):
          pass
    return out

  @staticmethod
  def _ptt_ok(lines: list[str], want: str) -> bool:
    return any("OK" in ln and "PTT" in ln and want in ln for ln in lines)

  def sync_time(self) -> None:
    ms = int(time.time() * 1000)
    self._cmd(f"TIME {ms}")

  def ptt_on(self) -> bool:
    lines = self._cmd("PTT 1")
    ok = self._ptt_ok(lines, "1")
    if not ok:
      self.last_error = f"PTT 1 nincs OK: {lines!r}"
    return ok

  def ptt_off(self) -> bool:
    lines = self._cmd("PTT 0")
    ok = self._ptt_ok(lines, "0")
    if not ok:
      self.last_error = f"PTT 0 nincs OK: {lines!r}"
    return ok

  @staticmethod
  def _cmd_ok(lines: list[str], token: str) -> bool:
    return any("OK" in ln and token in ln for ln in lines)

  def shutdown(self) -> bool:
    """PTT OFF + ESP biztonsági tiltás + soros lezárás."""
    with self._lock:
      ok = False
      for _ in range(3):
        lines = self._cmd_unlocked("PTT 0")
        ok = self._ptt_ok(lines, "0") or ok
      lines = self._cmd_unlocked("SHUTDOWN")
      ok = self._cmd_ok(lines, "SHUTDOWN") or ok
      if self._ser is not None:
        try:
          self._ser.close()
        except Exception:
          pass
        self._ser = None
      return ok

  def resume(self) -> bool:
    """Tiltás feloldása — újranyitja a portot ha le volt zárva."""
    lines = self._cmd("RESUME")
    if self._cmd_ok(lines, "RESUME"):
      return True
    return self.ping()

  def close(self) -> None:
    with self._lock:
      if self._ser is not None:
        try:
          self._cmd_unlocked("PTT 0")
          self._ser.close()
        except Exception:
          pass
        self._ser = None


def make_ptt(port: str | None, *, simulate: bool = False) -> PttBackend:
  if simulate or not port:
    return NullPtt()
  return Esp32Ptt(port)
