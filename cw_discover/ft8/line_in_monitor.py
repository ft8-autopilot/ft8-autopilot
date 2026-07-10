"""Line-in jelszint felügyelet — 30 s-enként mintavétel, TX tiltás alacsony jelnél."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import numpy as np

LINE_IN_MIN_RMS = 0.3
CHECK_INTERVAL_S = 30.0
SAMPLE_SEC = 0.4
CAPTURE_FS = 48_000

MeasureFn = Callable[[str, float], float]
RmsProvider = Callable[[], float]


def _stereo_peak_rms(left: np.ndarray, right: np.ndarray) -> float:
  lr = float(np.sqrt(np.mean(left * left)))
  rr = float(np.sqrt(np.mean(right * right)))
  return max(lr, rr)


def measure_line_in_rms(pulse_name: str, duration_s: float = SAMPLE_SEC) -> float:
  """Rövid Pulse minta — max(L,R) RMS (nyers bemenet, auto-gain előtt)."""
  from cw_discover.audio.stereo_capture import StereoPulseCapture

  cap = StereoPulseCapture(pulse_name, CAPTURE_FS)
  cap.start()
  deadline = time.monotonic() + max(0.15, duration_s)
  peak = 0.0
  try:
    while time.monotonic() < deadline:
      chunk = cap.read(timeout=0.12)
      if chunk is None:
        continue
      peak = max(peak, _stereo_peak_rms(*chunk))
  finally:
    cap.stop()
  return peak


class LineInMonitor:
  """Háttérszál: fél percenként ellenőrzi a line-in RMS-t."""

  def __init__(
    self,
    pulse_name: str,
    *,
    on_change: Callable[[bool, float], None] | None = None,
    rms_provider: RmsProvider | None = None,
    measure: MeasureFn | None = None,
    min_rms: float = LINE_IN_MIN_RMS,
    interval_s: float = CHECK_INTERVAL_S,
  ) -> None:
    self.pulse_name = pulse_name
    self._on_change = on_change
    self._rms_provider = rms_provider
    self._measure = measure or measure_line_in_rms
    self._min_rms = min_rms
    self._interval_s = interval_s
    self._ok = True
    self._last_rms = 0.0
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  @property
  def signal_ok(self) -> bool:
    with self._lock:
      return self._ok

  @property
  def last_rms(self) -> float:
    with self._lock:
      return self._last_rms

  def tx_allowed(self) -> bool:
    return self.signal_ok

  def start(self) -> None:
    self.stop()
    self._stop.clear()
    with self._lock:
      self._ok = True
      self._last_rms = 0.0
    self._thread = threading.Thread(target=self._loop, daemon=True, name="line-in-monitor")
    self._thread.start()

  def stop(self) -> None:
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=self._interval_s + 2.0)
      self._thread = None

  def check_once(self) -> tuple[bool, float]:
    return self.evaluate(self._read_rms())

  def evaluate(self, rms: float) -> tuple[bool, float]:
    ok = rms >= self._min_rms
    self._apply_state(ok, rms)
    return ok, rms

  def _read_rms(self) -> float:
    if self._rms_provider is not None:
      return float(self._rms_provider())
    return self._measure(self.pulse_name, SAMPLE_SEC)

  def _loop(self) -> None:
    while not self._stop.is_set():
      try:
        self.check_once()
      except Exception:
        pass
      if self._stop.wait(self._interval_s):
        break

  def _apply_state(self, ok: bool, rms: float) -> None:
    with self._lock:
      prev_ok = self._ok
      self._ok = ok
      self._last_rms = rms
    if ok != prev_ok and self._on_change is not None:
      self._on_change(ok, rms)
