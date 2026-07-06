"""TX biztonság — ragadó PTT leállítás + vonalkimenet zárolás."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import sounddevice as sd

from cw_discover.ft8.decode_meta import time_iso_utc
from cw_discover.ft8.ptt_client import PttBackend

log = logging.getLogger(__name__)

MAX_CONTINUOUS_PTT_SECONDS = 20.0
WATCHDOG_POLL_SECONDS = 0.5
LINE_GUARD_POLL_SECONDS = 1.0
PACTL_BACKOFF_SECONDS = 12.0
from cw_discover.paths import TX_LOG

# Vonalkimenet (FT-817 audio) — ne a régi .3 suffix
LINE_OUT_SINK = "alsa_output.pci-0000_00_1f.3.analog-stereo"

_pactl_lock = threading.Lock()
_pactl_backoff_until = 0.0


def _log_safety(event: str, detail: str = "") -> None:
  try:
    TX_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time_iso_utc(time.time())
    line = f"{ts} {event}"
    if detail:
      line += f" | {detail}"
    with TX_LOG.open("a", encoding="utf-8") as fh:
      fh.write(line + "\n")
  except OSError:
    pass


def _pactl(*args: str, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
  global _pactl_backoff_until
  with _pactl_lock:
    if time.monotonic() < _pactl_backoff_until:
      return subprocess.CompletedProcess(
        args=["pactl", *args], returncode=124, stdout="", stderr="backoff"
      )
    try:
      proc = subprocess.run(
        ["pactl", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
      )
    except subprocess.TimeoutExpired:
      _pactl_backoff_until = time.monotonic() + PACTL_BACKOFF_SECONDS
      log.warning("pactl timeout: %s", " ".join(args))
      try:
        from cw_discover.gui.error_journal import report_error

        report_error("audio_pactl_timeout", " ".join(args))
      except Exception:
        pass
      return subprocess.CompletedProcess(args=["pactl", *args], returncode=124, stdout="", stderr="timeout")
    return proc


def _default_sink() -> str:
  out = _pactl("get-default-sink")
  return out.stdout.strip() if out.returncode == 0 else ""


def _list_sinks() -> list[tuple[int, str]]:
  sinks: list[tuple[int, str]] = []
  out = _pactl("list", "sinks", "short")
  if out.returncode != 0:
    return sinks
  for line in out.stdout.splitlines():
    parts = line.split("\t")
    if len(parts) >= 2:
      try:
        sinks.append((int(parts[0]), parts[1]))
      except ValueError:
        continue
  return sinks


def _sink_index(name: str) -> int | None:
  for idx, sink_name in _list_sinks():
    if sink_name == name:
      return idx
  return None


def _fallback_sink(exclude: str) -> str:
  for _idx, name in _list_sinks():
    if name != exclude:
      return name
  return exclude


def _sink_input_rows() -> list[dict]:
  """Sink-input lista — JSON, hibás karakterekkel is."""
  proc = _pactl("-f", "json", "list", "sink-inputs")
  if proc.returncode != 0 or not proc.stdout.strip():
    return []
  try:
    return json.loads(proc.stdout)
  except json.JSONDecodeError:
    return []


def _input_pid(row: dict) -> int | None:
  props = row.get("properties") or {}
  for key in ("application.process.id", "window.process.id"):
    raw = props.get(key)
    if raw is None:
      continue
    try:
      return int(str(raw).strip('"'))
    except ValueError:
      continue
  return None


class PttWatchdog:
  """Ragadó PTT: 20 mp folyamatos adás → kényszerleállítás."""

  def __init__(
    self,
    ptt: PttBackend,
    *,
    enabled: bool = True,
    on_emergency: Callable[[str], None] | None = None,
  ) -> None:
    self._ptt = ptt
    self._enabled = enabled
    self._on_emergency = on_emergency
    self._lock = threading.Lock()
    self._ptt_on_since: float | None = None
    self._triggered = False
    self._stop = threading.Event()
    self._thread = threading.Thread(target=self._run, daemon=True, name="ptt-watchdog")

  def start(self) -> None:
    if self._enabled:
      self._thread.start()

  def stop(self) -> None:
    self._stop.set()
    if self._thread.is_alive():
      self._thread.join(timeout=2.0)

  def reset(self) -> None:
    with self._lock:
      self._triggered = False
      self._ptt_on_since = None
    self._stop.clear()
    if self._enabled and not self._thread.is_alive():
      self._thread = threading.Thread(target=self._run, daemon=True, name="ptt-watchdog")
      self._thread.start()

  def set_enabled(self, on: bool) -> None:
    self._enabled = on
    if not on:
      self.note_ptt_off()

  def note_ptt_on(self) -> None:
    with self._lock:
      if self._ptt_on_since is None:
        self._ptt_on_since = time.monotonic()

  def note_ptt_off(self) -> None:
    with self._lock:
      self._ptt_on_since = None

  def _run(self) -> None:
    while not self._stop.wait(WATCHDOG_POLL_SECONDS):
      with self._lock:
        since = self._ptt_on_since
        triggered = self._triggered
      if since is None or triggered:
        continue
      elapsed = time.monotonic() - since
      if elapsed >= MAX_CONTINUOUS_PTT_SECONDS:
        self._emergency_stop(elapsed)

  def _emergency_stop(self, elapsed: float) -> None:
    if not self._enabled:
      return
    with self._lock:
      if self._triggered:
        return
      self._triggered = True
    detail = f"{elapsed:.1f}s > {MAX_CONTINUOUS_PTT_SECONDS:.0f}s"
    _log_safety("SAFETY_PTT_STUCK", detail)
    log.error("PTT stuck — emergency stop (%s)", detail)
    try:
      sd.stop()
    except Exception:
      pass
    for _ in range(3):
      try:
        self._ptt.ptt_off()
      except Exception:
        pass
      time.sleep(0.1)
    if self._on_emergency is not None:
      try:
        self._on_emergency(detail)
      except Exception:
        pass


class WatchdogPtt:
  """PTT wrapper — watchdog jelzés minden kulcsolásnál."""

  last_error: str = ""

  def __init__(self, inner: PttBackend, watchdog: PttWatchdog) -> None:
    self._inner = inner
    self._watchdog = watchdog

  def ptt_on(self) -> bool:
    ok = self._inner.ptt_on()
    self.last_error = getattr(self._inner, "last_error", "")
    if ok:
      self._watchdog.note_ptt_on()
    return ok

  def ptt_off(self) -> bool:
    self._watchdog.note_ptt_off()
    ok = self._inner.ptt_off()
    self.last_error = getattr(self._inner, "last_error", "")
    return ok

  def sync_time(self) -> None:
    self._inner.sync_time()

  def ping(self) -> bool:
    fn = getattr(self._inner, "ping", None)
    if callable(fn):
      return bool(fn())
    return True

  def close(self) -> None:
    self._watchdog.note_ptt_off()
    fn = getattr(self._inner, "close", None)
    if callable(fn):
      fn()

  def shutdown(self) -> bool:
    self._watchdog.note_ptt_off()
    fn = getattr(self._inner, "shutdown", None)
    if callable(fn):
      return bool(fn())
    return self._inner.ptt_off()

  def resume(self) -> bool:
    fn = getattr(self._inner, "resume", None)
    if callable(fn):
      return bool(fn())
    return True


class LineOutGuard:
  """Vonalkimenet — más alkalmazások ne használják futás közben."""

  def __init__(self, *, line_sink: str = LINE_OUT_SINK, enabled: bool = True) -> None:
    self._line_sink = line_sink
    self._enabled = enabled
    self._pid = os.getpid()
    self._line_index: int | None = None
    self._saved_default = ""
    self._fallback_sink = ""
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None
    self._active = False

  @property
  def line_sink(self) -> str:
    return self._line_sink

  @property
  def active(self) -> bool:
    return self._active

  def set_enabled(self, on: bool) -> None:
    self._enabled = on
    if not on:
      self.release()

  def acquire(self) -> None:
    if not self._enabled or self._active:
      return
    self._line_index = _sink_index(self._line_sink)
    if self._line_index is None:
      log.warning("Line-out sink nem található: %s", self._line_sink)
      try:
        from cw_discover.gui.error_journal import report_error

        report_error("audio_line_sink_missing", self._line_sink)
      except Exception:
        pass
      return
    self._saved_default = _default_sink()
    self._fallback_sink = _fallback_sink(self._line_sink)
    if self._saved_default == self._line_sink and self._fallback_sink != self._line_sink:
      _pactl("set-default-sink", self._fallback_sink)
    self._evict_foreign_inputs()
    self._active = True
    self._stop.clear()
    self._thread = threading.Thread(target=self._run, daemon=True, name="line-out-guard")
    self._thread.start()
    _log_safety("LINE_GUARD_ON", self._line_sink)

  def release(self) -> None:
    if not self._active:
      return
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=2.0)
      self._thread = None
    if self._saved_default:
      _pactl("set-default-sink", self._saved_default)
    self._active = False
    _log_safety("LINE_GUARD_OFF", self._line_sink)

  def _run(self) -> None:
    while not self._stop.wait(LINE_GUARD_POLL_SECONDS):
      self._evict_foreign_inputs()

  def _evict_foreign_inputs(self) -> None:
    if self._line_index is None:
      return
    for row in _sink_input_rows():
      if int(row.get("sink", -1)) != self._line_index:
        continue
      pid = _input_pid(row)
      if pid is not None and pid == self._pid:
        continue
      idx = row.get("index")
      if idx is None:
        continue
      if self._fallback_sink and self._fallback_sink != self._line_sink:
        moved = _pactl("move-sink-input", str(idx), self._fallback_sink)
        if moved.returncode != 0:
          _pactl("kill-sink-input", str(idx))
      else:
        _pactl("kill-sink-input", str(idx))


def wrap_ptt_with_watchdog(
  ptt: PttBackend,
  *,
  enabled: bool = True,
  on_emergency: Callable[[str], None] | None = None,
) -> tuple[PttBackend, PttWatchdog]:
  watchdog = PttWatchdog(ptt, enabled=enabled, on_emergency=on_emergency)
  return WatchdogPtt(ptt, watchdog), watchdog
