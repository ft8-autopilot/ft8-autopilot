"""FT8 hang lejátszás + PTT — PyFT8 jelgenerálás, sounddevice."""
from __future__ import annotations

import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import os
import sounddevice as sd
from functools import lru_cache
from PyFT8.time_utils import global_time_utils
from PyFT8.transmitter import AudioOut

from cw_discover.ft8.decode_meta import message_stripped, time_iso_utc
from cw_discover.ft8.ft8_slot import seconds_until_tx_period
from cw_discover.ft8.ptt_client import PttBackend, NullPtt
from cw_discover.ft8.tx_safety import LINE_OUT_SINK, LineOutGuard

MAX_TX_START_SECONDS = 2.5
FS = 12_000
FT8_TONE_STEP = 6.25
FT8_HZ_MIN = 300.0
FT8_HZ_MAX = 3000.0
from cw_discover.paths import TX_LOG


@lru_cache(maxsize=512)
def snap_ft8_hz(hz: float) -> float:
  """FT8 tone rács (6,25 Hz) — tisztább jel, WSJT-X kompatibilis."""
  return round(float(hz) / FT8_TONE_STEP) * FT8_TONE_STEP


@dataclass
class TxResult:
  message: str
  audio_hz: float
  ok: bool
  error: str = ""


class Ft8TxPlayer:
  def __init__(
    self,
    *,
    ptt: PttBackend | None = None,
    audio_device: str = "pulse",
    amplitude: float = 0.45,
    simulate: bool = False,
    on_state: Callable[[bool, str, str], None] | None = None,
    line_guard: LineOutGuard | None = None,
    line_in_guard: Callable[[], bool] | None = None,
  ) -> None:
    self.ptt = ptt or NullPtt()
    self.audio_device = audio_device
    self.amplitude = amplitude
    self.simulate = simulate
    self.on_state = on_state
    self._line_guard = line_guard
    self._line_in_guard = line_in_guard
    self._ao = AudioOut()
    self._lock = threading.Lock()
    self._line_out_ready = False

  def _log_tx(self, event: str, message: str, detail: str = "") -> None:
    try:
      TX_LOG.parent.mkdir(parents=True, exist_ok=True)
      ts = time_iso_utc(time.time())
      line = f"{ts} {event} {message}"
      if detail:
        line += f" | {detail}"
      with TX_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    except OSError:
      pass

  def _emit_state(self, active: bool, message: str, error: str = "") -> None:
    if self.on_state is not None:
      self.on_state(active, message, error)

  def _line_sink(self) -> str:
    if self._line_guard is not None:
      return self._line_guard.line_sink
    return LINE_OUT_SINK

  def _ensure_line_out(self) -> None:
    if self._line_out_ready:
      return
    sink = self._line_sink()
    subprocess.run(["pactl", "set-sink-mute", sink, "0"], check=False, capture_output=True)
    subprocess.run(["pactl", "set-sink-volume", sink, "90%"], check=False, capture_output=True)
    self._line_out_ready = True

  def wait_for_tx_slot(
    self,
    tx_period: int | None = None,
    *,
    should_abort: Callable[[], bool] | None = None,
  ) -> None:
    def aborted() -> bool:
      return should_abort is not None and should_abort()

    if tx_period is not None:
      while not aborted():
        delay = seconds_until_tx_period(tx_period)
        if delay <= 0:
          return
        time.sleep(min(delay, 0.25 if delay > 1.0 else delay))
      return
    while not aborted():
      ct = global_time_utils.cycle_time()
      if ct <= MAX_TX_START_SECONDS:
        return
      delay = 15.25 - ct
      if delay <= 0:
        return
      time.sleep(min(delay, 0.25))

  def build_wave(self, message: str, audio_hz: float) -> np.ndarray | None:
    parts = message_stripped(message).split()
    if len(parts) < 3:
      return None
    symbols = self._ao.create_ft8_symbols(message)
    if not any(symbols):
      return None
    hz = snap_ft8_hz(audio_hz)
    pcm = self._ao.create_ft8_wave(symbols, fs=FS, f_base=hz, amplitude=self.amplitude)
    return pcm.astype(np.float32) / 32767.0

  @contextmanager
  def _pulse_sink_env(self):
    """TX mindig a vonalkimenetre — default sink lehet HDMI (LineOutGuard)."""
    sink = self._line_sink()
    prev = os.environ.get("PULSE_SINK")
    os.environ["PULSE_SINK"] = sink
    try:
      yield
    finally:
      if prev is None:
        os.environ.pop("PULSE_SINK", None)
      else:
        os.environ["PULSE_SINK"] = prev

  @staticmethod
  def _mono_to_stereo(mono: np.ndarray) -> np.ndarray:
    """Pulse/sounddevice mono → csak balra megy; L+R duplikálás a vonalkimenetre."""
    m = np.asarray(mono, dtype=np.float32).reshape(-1)
    return np.column_stack([m, m])

  @staticmethod
  def halt_audio() -> None:
    """Hang leállítás — Stop / kilépés / epoch megszakítás (nem blokkol)."""
    try:
      sd.stop()
    except Exception:
      pass

  def force_ptt_off(self, attempts: int = 3) -> None:
    for i in range(attempts):
      try:
        self.ptt.ptt_off()
      except Exception:
        pass
      if i + 1 < attempts:
        time.sleep(0.03)

  def _play_interruptible(
    self,
    stereo: np.ndarray,
    *,
    should_abort: Callable[[], bool] | None = None,
  ) -> bool:
    """True = teljes lejátszás, False = megszakítva (sd.stop / should_abort)."""
    duration = len(stereo) / FS
    min_ok = duration * 0.92
    started = time.monotonic()
    sd.play(stereo, FS, device=self.audio_device, blocking=False)
    end_at = started + duration + 0.35
    while time.monotonic() < end_at:
      if should_abort is not None and should_abort():
        self.halt_audio()
        return False
      try:
        stream = sd.get_stream()
        if stream is None or not stream.active:
          if time.monotonic() - started >= min_ok:
            return True
          self.halt_audio()
          return False
      except Exception:
        if time.monotonic() - started >= min_ok:
          return True
        self.halt_audio()
        return False
      time.sleep(0.02)
    self.halt_audio()
    return True

  def transmit(
    self,
    message: str,
    audio_hz: float,
    *,
    tx_period: int | None = None,
    should_abort: Callable[[], bool] | None = None,
  ) -> TxResult:
    if self._line_in_guard is not None and not self._line_in_guard():
      self._log_tx("TX_BLOCK", message, "line_in_low")
      return TxResult(message=message, audio_hz=audio_hz, ok=False, error="line_in_blocked")

    if self.simulate:
      with self._lock:
        if should_abort is not None and should_abort():
          self._log_tx("TX_CANCEL", message)
          return TxResult(message=message, audio_hz=audio_hz, ok=False, error="cancelled")
        self._log_tx("SIM_OK", message)
        return TxResult(message=message, audio_hz=audio_hz, ok=True)

    wave = self.build_wave(message, audio_hz)
    if wave is None:
      self._log_tx("ENCODE_FAIL", message)
      return TxResult(message=message, audio_hz=audio_hz, ok=False, error="encode_failed")

    self.wait_for_tx_slot(tx_period, should_abort=should_abort)
    if should_abort is not None and should_abort():
      self._log_tx("TX_CANCEL", message)
      return TxResult(message=message, audio_hz=audio_hz, ok=False, error="cancelled")
    self._ensure_line_out()
    period_note = f" p{tx_period}" if tx_period is not None else ""
    self._emit_state(True, message)
    self._log_tx("TX_START", message, f"{audio_hz:.0f} Hz{period_note}")
    aborted = False
    ptt_err = ""
    try:
      if not self.ptt.ptt_on():
        ptt_err = getattr(self.ptt, "last_error", "") or "ptt_on_failed"
        self._log_tx("PTT_FAIL", message, ptt_err)
        return TxResult(message=message, audio_hz=audio_hz, ok=False, error=ptt_err)
      stereo = self._mono_to_stereo(wave)
      with self._pulse_sink_env():
        if should_abort is not None and should_abort():
          aborted = True
        else:
          sd.play(stereo, FS, device=self.audio_device, blocking=True)
    except Exception as exc:
      self._log_tx("AUDIO_FAIL", message, str(exc))
      return TxResult(message=message, audio_hz=audio_hz, ok=False, error=str(exc))
    finally:
      self.force_ptt_off()
      err_note = "aborted" if aborted else ptt_err
      self._emit_state(False, message, err_note)
    if aborted:
      self._log_tx("TX_ABORT", message)
      return TxResult(message=message, audio_hz=audio_hz, ok=False, error="aborted")
    self._log_tx("TX_OK", message)
    return TxResult(message=message, audio_hz=audio_hz, ok=True)
