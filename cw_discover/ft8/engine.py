"""PyFT8 Receiver motor — WSJT-X kompatibilis LDPC mátrix + pro naplózás."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from PyFT8.receiver import AudioIn

from cw_discover.ft8.audio_feed import Ft8AudioFeed
from cw_discover.ft8.decode_meta import dsp_from_candidate, message_upper, time_iso_utc
from cw_discover.ft8.ft8_protocol import normalize_directed_call_order, snr_report_text
from cw_discover.ft8.receiver_instrumented import InstrumentedReceiver

DEFAULT_LINEIN = "alsa_input.pci-0000_00_1f.3.analog-stereo"
FT8_AUDIO_MAX_HZ = 3100
FT8_AUDIO_MIN_HZ = 200


@dataclass
class AudioSnapshot:
  raw_rms: float = 0.0
  out_rms: float = 0.0
  peak: float = 0.0
  clip_frac: float = 0.0
  gain: float = 1.0

  def to_log_dict(self) -> dict:
    return {
      "raw_rms": round(self.raw_rms, 5),
      "out_rms": round(self.out_rms, 5),
      "peak": round(self.peak, 4),
      "clip_frac": round(self.clip_frac, 4),
      "gain": round(self.gain, 4),
    }


@dataclass
class DecodeReport:
  cycle: str
  snr: int
  dt: float
  audio_hz: int
  rf_khz: float
  message: str
  time_received: float
  cycle_start_utc: str = ""
  dsp: dict = field(default_factory=dict)
  audio: dict = field(default_factory=dict)

  @property
  def wsjtx_line(self) -> str:
    return f"{self.cycle} {snr_report_text(self.snr)} {self.dt:4.1f} {self.audio_hz:4.0f} ~ {self.message}"


class Ft8Engine:
  def __init__(
    self,
    dial_mhz: float = 7.074,
    band: str = "40m",
    pulse_name: str = DEFAULT_LINEIN,
    on_decode: callable | None = None,
    on_levels: callable | None = None,
    on_candidate: callable | None = None,
    on_cycle_search: callable | None = None,
    my_callsign: str = "",
    my_grid4: str = "",
  ) -> None:
    self.dial_mhz = dial_mhz
    self._dial_hz = dial_mhz * 1_000_000
    self.band = band
    self.pulse_name = pulse_name
    self._on_decode = on_decode
    self._on_levels = on_levels
    self._on_candidate = on_candidate
    self._on_cycle_search = on_cycle_search
    self._my_callsign = my_callsign.strip().upper()
    self._my_grid4 = my_grid4.strip().upper()[:4]
    self._seen: OrderedDict[str, None] = OrderedDict()
    self._lock = threading.Lock()
    self._audio_snap = AudioSnapshot()
    self.audio_in = AudioIn(FT8_AUDIO_MAX_HZ)
    self.receiver = InstrumentedReceiver(
      self.audio_in,
      [FT8_AUDIO_MIN_HZ, FT8_AUDIO_MAX_HZ],
      self._handle_decode,
      on_candidate=self._handle_candidate,
      on_cycle_search=self._handle_cycle_search,
    )
    self.feed = Ft8AudioFeed(self.audio_in, pulse_name, on_levels=self._handle_levels)
    self.running = False

  def _handle_levels(self, raw_rms: float, out_rms: float, peak: float, clip_frac: float, gain: float) -> None:
    self._audio_snap = AudioSnapshot(raw_rms, out_rms, peak, clip_frac, gain)
    if self._on_levels is not None:
      self._on_levels(raw_rms, out_rms, peak, clip_frac, gain)

  def _handle_candidate(self, candidate: Any, cycle: str) -> None:
    if self._on_candidate is None:
      return
    self._on_candidate(candidate, cycle, time.time(), self._audio_snap)

  def _handle_cycle_search(
    self, cycle: str, cycle_start_time: float, n_candidates: int, busy_max: float | None
  ) -> None:
    if self._on_cycle_search is None:
      return
    self._on_cycle_search(
      cycle, cycle_start_time, n_candidates, busy_max, time.time(), self._audio_snap
    )

  def _handle_decode(self, candidate) -> None:
    if not candidate.msg:
      return
    cycle = candidate.cyclestart.get("string", "") if getattr(candidate, "cyclestart", None) else ""
    if not cycle:
      return
    msg = candidate.msg.strip()
    if self._my_callsign:
      msg = normalize_directed_call_order(msg, self._my_callsign, my_grid4=self._my_grid4)
    msg_norm = message_upper(msg)
    key = f"{cycle}|{msg_norm}"
    with self._lock:
      if key in self._seen:
        return
      self._seen[key] = None
      if len(self._seen) > 5000:
        self._seen.popitem(last=False)
      snap = self._audio_snap
    rf_khz = (self._dial_hz + candidate.fHz) / 1000.0
    now = time.time()
    cycle_ts = candidate.cyclestart.get("time", now) if getattr(candidate, "cyclestart", None) else now
    report = DecodeReport(
      cycle=cycle,
      snr=int(candidate.snr),
      dt=float(candidate.dt),
      audio_hz=int(candidate.fHz),
      rf_khz=rf_khz,
      message=msg,
      time_received=now,
      cycle_start_utc=time_iso_utc(cycle_ts),
      dsp=dsp_from_candidate(candidate),
      audio=snap.to_log_dict(),
    )
    if self._on_decode is not None:
      self._on_decode(report)

  def start(self) -> None:
    if self.running:
      return
    self.audio_in.sync_pointer_to_wall_clock()
    self.feed.start()
    self.running = True

  def stop(self) -> None:
    if not self.running:
      return
    self.feed.stop()
    self.running = False

  def set_station_identity(self, callsign: str, grid4: str = "") -> None:
    self._my_callsign = callsign.strip().upper()
    self._my_grid4 = grid4.strip().upper()[:4]

  def set_dial_mhz(self, mhz: float) -> None:
    self.dial_mhz = mhz
    self._dial_hz = mhz * 1_000_000

  def set_rx_paused(self, paused: bool) -> None:
    self.feed.set_rx_paused(paused)

  def get_audio_settings(self) -> dict:
    return {
      "gain_auto": self.feed.gain_auto,
      "gain_manual": self.feed.gain_manual,
      "target_rms": self.feed.target_rms,
      "pulse_device": self.pulse_name,
    }
