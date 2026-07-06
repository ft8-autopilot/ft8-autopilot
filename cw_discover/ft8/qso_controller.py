"""FT8 automata QSO — CQ, válasz, napló (PyFT8 progress_qso alap)."""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from cw_discover.ft8.engine import DecodeReport
from cw_discover.ft8.forgalmi_log import ForgalmiNaplo, QsoRecord
from cw_discover.ft8.grid_geo import _call_key, grid4_upper, station_dist_for_g4
from cw_discover.ft8.ft8_protocol import (
  MessageTriplet,
  is_73,
  is_grid_token,
  is_r_report,
  is_report,
  is_rr73,
  is_rrr,
  message_triplet,
  rst_from_report_token,
  snr_report_text,
  valid_remote_call,
)
from cw_discover.ft8.ft8_slot import (
  cycle_key_at,
  decode_is_fresh,
  ft8_period_at,
  opposite_period,
  period_from_cycle,
  tx_slot_id,
)
from cw_discover.ft8.home_qth import DEFAULT_HOME, HOME_LAT, HOME_LON
from cw_discover.ft8.pro_operator import (
  ContactIntelCache,
  PriorityMode,
  ProOperatorConfig,
  geo_distance_for_cq,
  pick_best_cq,
  score_cq_candidate,
  score_incoming_candidate,
)
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.ft8.tx_player import Ft8TxPlayer, snap_ft8_hz


class QsoPhase(str, Enum):
  IDLE = "idle"
  CALLING_CQ = "calling_cq"
  ACTIVE = "active"
  CLOSING = "closing"


@dataclass
class ActiveQso:
  remote_call: str
  remote_grid: str = ""
  audio_hz: float = 1500.0
  rst_sent: str = ""
  rst_rcvd: str = ""
  time_on: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
  cycles_without_reply: int = 0
  tx_period: int = 0  # csak ezen a sloton adunk (0=:00/:30, 1=:15/:45 UTC)
  phase: QsoPhase = QsoPhase.ACTIVE


class Ft8AutoOperator:
  """
  Automata FT8 operátor.

  Prioritás (WSJT-X / PyFT8 szokás):
  1. Aktív QSO folytatása (73-ig)
  2. Bejövő hívás (minket szólítanak) — magasabb, mint CQ-zás
  3. CQ válasz (SNR küszöb, nem dolgozott ma)
  4. Saját CQ: adás → várakozás (cq_repeat_cycles) → hívó választás → ismét CQ
  """

  MAX_RETRY_CYCLES = 3
  OUTBOUND_FAIL_COOLDOWN_SEC = 600  # sikertelen outbound → 10 perc CQ-vadászat off

  def __init__(
    self,
    *,
    station: StationIdentity | None = None,
    naplo: ForgalmiNaplo | None = None,
    tx: Ft8TxPlayer | None = None,
    simulate_tx: bool = False,
    on_status: Callable[[str], None] | None = None,
    on_tx: Callable[[str], None] | None = None,
  ) -> None:
    self.station = station or StationIdentity.load()
    self.naplo = naplo or ForgalmiNaplo(station=self.station)
    self.tx = tx or Ft8TxPlayer(simulate=simulate_tx)
    self._me_callsign = _call_key(self.station.callsign)
    self._my_grid4 = self.station.grid4
    self._on_status = on_status or (lambda _s: None)
    self._on_tx = on_tx or (lambda _s: None)
    self.armed = False
    self.band = "40m"
    self.dial_mhz = 7.074
    self._active: ActiveQso | None = None
    self._phase = QsoPhase.IDLE
    self._cq_wait_remaining = 0
    self._last_tx_msg = ""
    self._default_tx_hz = snap_ft8_hz(1500.0)
    self._intel = ContactIntelCache()
    self._outbound_cooldown: dict[str, float] = {}
    self._cq_buffer: list = []
    self._incoming_buffer: list = []
    self._cq_only_mode = False
    self._last_cycle_key = ""
    self._cq_tx_period: int | None = None
    self._tx_queue: queue.Queue[tuple[str, float, int, int] | None] = queue.Queue()
    self._queued_slot_id = ""
    self._tx_epoch = 0
    self._worker = threading.Thread(target=self._tx_worker, daemon=True, name="ft8-tx")
    self._worker.start()
    self._lock = threading.RLock()

  def set_band(self, band: str, dial_mhz: float) -> None:
    self.band = band
    self.dial_mhz = dial_mhz

  def set_pro_config(self, config: ProOperatorConfig) -> None:
    self.station.pro = config

  def set_cq_only_mode(self, on: bool) -> None:
    """CQ üzem: csak saját CQ + ránk hívók (idegen CQ ignor)."""
    with self._lock:
      self._cq_only_mode = on
      self._cq_buffer.clear()
      self._incoming_buffer.clear()
    self._status("CQ üzem ON" if on else "CQ üzem OFF")

  @property
  def cq_only_mode(self) -> bool:
    return self._cq_only_mode

  def abort_qso(self, reason: str = "") -> None:
    with self._lock:
      old = self._active.remote_call if self._active else ""
      self._active = None
      self._phase = QsoPhase.IDLE
      self._cq_wait_remaining = 0
      self._last_tx_msg = ""
      self._cq_buffer.clear()
      self._incoming_buffer.clear()
      self._drain_tx_queue()
      self._queued_slot_id = ""
    self._halt_rf()
    if old:
      self._status(f"Feladva: {old}" + (f" ({reason})" if reason else ""))

  def _halt_rf(self) -> None:
    """PTT + hang azonnali leállítás (Stop, kilépés, abort)."""
    self.tx.halt_audio()
    self.tx.force_ptt_off()

  def halt_transmission(self, reason: str = "") -> None:
    """Teljes TX leállítás — QSO közben is, várakozó sor és aktív adás."""
    with self._lock:
      self.armed = False
      self._active = None
      self._phase = QsoPhase.IDLE
      self._cq_wait_remaining = 0
      self._last_tx_msg = ""
      self._cq_buffer.clear()
      self._incoming_buffer.clear()
      self._drain_tx_queue()
      self._queued_slot_id = ""
    self._halt_rf()
    if reason:
      self._status(f"TX leállítva ({reason})")

  def engage_call(
    self,
    call: str,
    audio_hz: float,
    *,
    rx_report: str = "",
    rx_snr: int = 0,
  ) -> None:
    """Kényszerített QSO — pl. bejövő hívás PRO váltás."""
    call = _call_key(call)
    with self._lock:
      self._active = None
      self._phase = QsoPhase.IDLE
      self._cq_buffer.clear()
      self._incoming_buffer.clear()
    me = self._me_callsign
    grid = self._intel.grid_for(call)
    self._begin_qso(call, grid, audio_hz, heard_period=ft8_period_at())
    if rx_report and is_report(rx_report):
      self._active.rst_rcvd = rst_from_report_token(rx_report)
      rst = snr_report_text(rx_snr)
      self._active.rst_sent = rst
      self._queue_tx(f"{call} {me} R{rst}", audio_hz)
      self._status(f"PRO → {call} (jelentés {rx_report})")
    else:
      self._queue_tx(f"{call} {me} {self._my_grid4}", audio_hz)
      self._status(f"PRO → {call}")

  @staticmethod
  def _directed_to_me(triplet: MessageTriplet, me: str) -> str | None:
    """Bejövő hívás — a mi hívójelünk a második mezőben (DE call)."""
    if triplet.call_b == me and valid_remote_call(triplet.call_a):
      return triplet.call_a
    return None

  def _accepts_reversed_incoming(self) -> bool:
    """Fordított N0CALL REMOTE … — CQ üzemben (calling_cq vagy idle várakozás)."""
    if self._active is not None or not self.armed:
      return False
    if self._phase == QsoPhase.CALLING_CQ:
      return True
    return self._phase == QsoPhase.IDLE and self._cq_only_mode

  def _directed_to_me_reversed(self, triplet: MessageTriplet, me: str) -> str | None:
    """
    Fordított call sorrend: N0CALL REMOTE … — PyFT8/line-in néha megfordítja.
    CQ üzemben (calling_cq vagy idle), különben spill/ön-dekód.
    """
    if not self._accepts_reversed_incoming():
      return None
    if triplet.call_a != me or not valid_remote_call(triplet.call_b):
      return None
    third = triplet.third
    if is_grid_token(third) and third[:4] == self._my_grid4:
      return None
    if is_rr73(third) or is_73(third) or is_rrr(third):
      return None
    if self._is_self_tx_echo(triplet):
      return None
    if is_report(third) or is_grid_token(third):
      return triplet.call_b
    return None

  @staticmethod
  def _incoming_triplet(triplet: MessageTriplet, me: str, incoming: str) -> MessageTriplet:
    """CALL ME third — score_incoming_candidate és napló számára."""
    if triplet.call_a == incoming and triplet.call_b == me:
      return triplet
    return MessageTriplet(incoming, me, triplet.third)

  def _is_self_tx_echo(self, triplet: MessageTriplet) -> bool:
    """Saját TX visszahallás (call sorrend és report egyezik)."""
    if not self._last_tx_msg:
      return False
    last = message_triplet(self._last_tx_msg)
    if last is None or triplet.third != last.third:
      return False
    a, b = triplet.call_a, triplet.call_b
    la, lb = last.call_a, last.call_b
    return (a == la and b == lb) or (a == lb and b == la)

  @staticmethod
  def _triplet_is_active_remote(triplet: MessageTriplet, me: str, remote: str) -> bool:
    """REMOTE ME vagy ME REMOTE — FT8 dekód néha fordított call sorrend."""
    ru = _call_key(remote)
    return (triplet.call_a == ru and triplet.call_b == me) or (
      triplet.call_a == me and triplet.call_b == ru
    )

  def _ignore_me_first_decode(self, triplet: MessageTriplet, me: str) -> bool:
    """
    N0CALL … első mező — általában saját TX visszhang (line-in).
    Kivétel: aktív QSO partner report/zárás fordított sorrendben (N0CALL R3HX/P +05).
    Kivétel: CQ üzemben fordított bejövő (N0CALL IZ8PPI +00) — calling_cq vagy idle.
    """
    if triplet.call_a != me or not valid_remote_call(triplet.call_b):
      return False
    third = triplet.third
    if is_grid_token(third) and third[:4] == self._my_grid4:
      return True
    if self._active is not None and triplet.call_b == self._active.remote_call:
      if is_report(third) or is_r_report(third) or is_rr73(third) or is_73(third) or is_rrr(third):
        return False
    if self._directed_to_me_reversed(triplet, me) is not None:
      return False
    return True

  def _should_preempt_for_incoming(self, remote: str) -> bool:
    if self._active is None:
      return True
    if self._active.remote_call == remote:
      return False
    if not self.station.pro.enabled:
      return False
    return self._active.cycles_without_reply >= 1 or not self._active.rst_rcvd

  def set_cq_wait_periods(self, periods: int) -> None:
    """CQ-k közötti várakozási periódusok (1, 3, 5, 7, 9)."""
    from cw_discover.ft8.station_identity import normalize_cq_wait_periods

    periods = normalize_cq_wait_periods(periods)
    self.station.cq_repeat_cycles = periods
    self._status(f"CQ várakozás: {periods} periódus")

  def set_armed(self, on: bool) -> None:
    with self._lock:
      was = self.armed
      self.armed = on
      if not on:
        self._active = None
        self._phase = QsoPhase.IDLE
        self._cq_wait_remaining = 0
        self._cq_buffer.clear()
        self._incoming_buffer.clear()
        self._drain_tx_queue()
      elif not was:
        self._last_cycle_key = ""
        self._cq_tx_period = None
        self._cq_wait_remaining = 0
    mode = self._armed_mode_label()
    self._status("PTT OFF" if not on else f"PTT ARMED ({mode})")

  def _armed_mode_label(self) -> str:
    if self._cq_only_mode:
      return "CQ üzem"
    if self.station.pro.enabled:
      return "PRO"
    return "alap"

  def _mark_outbound_failed(self, call: str) -> None:
    """Outbound QSO feladva — ne hívjuk újra CQ-vadászattal (bejövő hívás OK)."""
    cu = _call_key(call)
    if not cu:
      return
    with self._lock:
      self._outbound_cooldown[cu] = time.monotonic() + self.OUTBOUND_FAIL_COOLDOWN_SEC

  def _clear_outbound_cooldown(self, call: str) -> None:
    with self._lock:
      self._outbound_cooldown.pop(_call_key(call), None)

  def _is_outbound_cooldown(self, call: str) -> bool:
    cu = _call_key(call)
    with self._lock:
      exp = self._outbound_cooldown.get(cu)
      if exp is None:
        return False
      if time.monotonic() >= exp:
        del self._outbound_cooldown[cu]
        return False
      return True

  def outbound_cooldown_calls(self) -> set[str]:
    """Outbound 10 perces szünet — GUI halvány kiemeléshez."""
    now = time.monotonic()
    with self._lock:
      active: set[str] = set()
      expired: list[str] = []
      for call, exp in self._outbound_cooldown.items():
        if now < exp:
          active.add(call)
        else:
          expired.append(call)
      for call in expired:
        del self._outbound_cooldown[call]
      return active

  @property
  def phase(self) -> QsoPhase:
    return self._phase

  def on_cycle(self, cycle: str, _ts: float) -> None:
    if not self.armed:
      return
    with self._lock:
      flushed = False
      new_cycle = cycle != self._last_cycle_key
      if new_cycle:
        if self._cq_only_mode:
          flushed = self._flush_incoming_buffer()
        else:
          flushed = self._flush_cq_buffer()
        self._last_cycle_key = cycle
      slot = ft8_period_at()
      if self._active is not None:
        if slot == self._active.tx_period:
          if flushed:
            return
          if self._phase == QsoPhase.CLOSING:
            self._active.cycles_without_reply += 1
            if self._active.cycles_without_reply > self.MAX_RETRY_CYCLES:
              self._status(f"Lezárás (nincs 73): {self._active.remote_call}")
              self._finish_qso()
              return
            last = message_triplet(self._last_tx_msg) if self._last_tx_msg else None
            if last is not None and is_rr73(last.third):
              self._queue_tx(self._last_tx_msg, self._active.audio_hz, is_retry=True)
            return
          self._active.cycles_without_reply += 1
          if self._active.cycles_without_reply > self.MAX_RETRY_CYCLES:
            failed = self._active.remote_call
            self._status(f"Feladás: {failed} (nincs válasz)")
            self._mark_outbound_failed(failed)
            self._active = None
            self._phase = QsoPhase.IDLE
            self._cq_wait_remaining = 0
          elif self._last_tx_msg:
            self._queue_tx(self._last_tx_msg, self._active.audio_hz, is_retry=True)
        return
      if new_cycle:
        if self._cq_tx_period is None:
          self._cq_tx_period = slot
        self._tick_cq_scheduler(slot)

  def _tick_cq_scheduler(self, slot: int) -> None:
    """CQ ütem: várakozás → hívó ellenőrzés (flush) → új CQ a saját TX periódusban."""
    if self._cq_wait_remaining > 0:
      self._cq_wait_remaining -= 1
      if self._cq_wait_remaining > 0:
        self._status(f"CQ várakozás ({self._cq_wait_remaining} periódus)")
      return
    if slot != self._cq_tx_period:
      return
    self._queue_cq()
    self._cq_wait_remaining = self.station.cq_repeat_cycles

  def _prune_candidate_buffer(self, buf: list) -> None:
    if not buf:
      return
    buf[:] = [c for c in buf if c.cycle and decode_is_fresh(c.cycle)]

  def _prune_cq_buffer(self) -> None:
    """Régi CQ jelöltek — ne válaszoljunk 30+ mp-es hallásra."""
    self._prune_candidate_buffer(self._cq_buffer)

  def _prune_incoming_buffer(self) -> None:
    self._prune_candidate_buffer(self._incoming_buffer)

  def _flush_cq_buffer(self) -> bool:
    self._prune_cq_buffer()
    if not self._cq_buffer or self._active is not None:
      self._cq_buffer.clear()
      return False
    eligible = [c for c in self._cq_buffer if not self._is_outbound_cooldown(c.call)]
    self._cq_buffer.clear()
    best = pick_best_cq(eligible)
    if best is None:
      return False
    self._answer_cq(best.call, best.grid, best.audio_hz, best.reason, best.snr, cycle=best.cycle)
    return True

  def _flush_incoming_buffer(self) -> bool:
    self._prune_incoming_buffer()
    if not self._incoming_buffer or self._active is not None:
      self._incoming_buffer.clear()
      return False
    best = pick_best_cq(self._incoming_buffer)
    self._incoming_buffer.clear()
    if best is None:
      return False
    self._answer_incoming(
      best.call,
      best.grid,
      best.audio_hz,
      None,
      best.snr,
      cycle=best.cycle,
      skip_our_grid=True,
      reason=best.reason,
    )
    return True

  def _note_intel(self, call: str, grid: str, snr: int, distance_km: float | None) -> None:
    if self.station.pro.enabled:
      self._intel.note_decode(call, grid, snr, distance_km)

  def _resolve_grid_dist(
    self, report: DecodeReport, call: str, *, third: str = ""
  ) -> tuple[str, float | None]:
    """Grid + távolság — intel cache előbb, geo_distance csak ha kell."""
    if third and is_grid_token(third):
      grid = grid4_upper(third)
    else:
      grid = self._intel.grid_for(call)
    dist = self._intel.distance_for(call)
    if grid and dist is not None:
      return grid, dist
    if grid:
      d = station_dist_for_g4(grid, HOME_LAT, HOME_LON)
      if d is not None:
        return grid, d
    grid_geo, dist_geo = geo_distance_for_cq(report, DEFAULT_HOME)
    return grid or grid_geo, dist if dist is not None else dist_geo

  def _maybe_adopt_cq_hz(self, report: DecodeReport, triplet: MessageTriplet, me: str) -> None:
    """CQ ugyanazon a hang Hz-en mint a QSO válaszok — sáv aktivitás követése."""
    if self._phase not in (QsoPhase.IDLE, QsoPhase.CALLING_CQ):
      return
    if triplet.is_cq and triplet.call_b == self._me_callsign:
      return
    hz = float(report.audio_hz)
    if hz < 300.0 or hz > 3000.0:
      return
    self._default_tx_hz = snap_ft8_hz(hz)

  def on_decode(self, report: DecodeReport) -> None:
    if not self.armed:
      return
    if report.cycle and not decode_is_fresh(report.cycle):
      return
    triplet = message_triplet(report.message)
    if triplet is None:
      return
    me = self._me_callsign
    self._maybe_adopt_cq_hz(report, triplet, me)
    if self._ignore_me_first_decode(triplet, me):
      return
    incoming = self._directed_to_me(triplet, me)
    if incoming is None:
      incoming = self._directed_to_me_reversed(triplet, me)
    with self._lock:
      if incoming and self._should_preempt_for_incoming(incoming):
        if self._active is not None and self._active.remote_call != incoming:
          self._status(f"PRO váltás → {incoming}")
          self._active = None
          self._phase = QsoPhase.IDLE
        if self._active is None:
          inc_triplet = self._incoming_triplet(triplet, me, incoming)
          if self._process_incoming_call(inc_triplet, report, me, incoming):
            return
      if self._active is not None and self._handle_active_exchange(triplet, report, me):
        return
      if triplet.is_cq:
        self._maybe_answer_cq(triplet, report)
        return
      if triplet.call_b == me and valid_remote_call(triplet.call_a):
        if self._active is not None:
          return
        remote = triplet.call_a
        inc_triplet = self._incoming_triplet(triplet, me, remote)
        self._process_incoming_call(inc_triplet, report, me, remote)

  def _process_incoming_call(
    self, triplet: MessageTriplet, report: DecodeReport, me: str, incoming: str
  ) -> bool:
    """Bejövő hívás (CALL ME …). True ha kezelve (nem kell tovább)."""
    third = triplet.third
    third_is_grid = is_grid_token(third)
    if is_rr73(third) or is_73(third):
      return True
    worked = self.naplo.recently_worked(incoming, band=self.band)
    if worked and not is_report(third):
      return True
    grid, dist = self._resolve_grid_dist(report, incoming, third=third)
    self._note_intel(incoming, grid, report.snr, dist)

    if is_report(third) and not is_r_report(third):
      self._answer_incoming(
        incoming, grid, report.audio_hz, third, report.snr, cycle=report.cycle
      )
      return True

    if not third_is_grid and not grid:
      return False

    if self._cq_only_mode:
      cand = score_incoming_candidate(
        report=report,
        triplet=triplet,
        grid=grid,
        distance_km=dist,
        worked=worked,
        config=self.station.pro,
      )
      if cand is not None:
        self._prune_incoming_buffer()
        self._incoming_buffer.append(cand)
        self._status(f"CQ üzem jelölt: {incoming} score={cand.score:.0f} ({cand.reason})")
      return True

    g4 = grid4_upper(grid or triplet.third)
    fast = self._prefer_fast_report() and bool(g4)
    self._answer_incoming(
      incoming,
      grid or triplet.third,
      report.audio_hz,
      None,
      report.snr,
      cycle=report.cycle,
      skip_our_grid=fast,
      reason="gyors riport" if fast else "",
    )
    return True

  def _prefer_fast_report(self) -> bool:
    pro = self.station.pro
    return pro.enabled and pro.priority == PriorityMode.STRONG_FAST

  def _maybe_answer_cq(self, triplet, report: DecodeReport) -> None:
    if self._cq_only_mode:
      return
    if self._active is not None:
      return
    call = triplet.call_b
    if not valid_remote_call(call):
      return
    pro = self.station.pro
    min_snr = pro.min_snr if pro.enabled else self.station.cq_min_snr
    if report.snr < min_snr:
      return
    if pro.enabled and report.snr > pro.max_snr:
      return
    if self.naplo.recently_worked(call, band=self.band):
      return
    if self._is_outbound_cooldown(call):
      return
    third = triplet.third
    grid, dist = self._resolve_grid_dist(report, call, third=third)
    self._note_intel(call, grid, report.snr, dist)

    if pro.enabled and pro.defer_cq_pick:
      cand = score_cq_candidate(
        report=report,
        triplet=triplet,
        grid=grid,
        distance_km=dist,
        worked=False,
        config=pro,
      )
      if cand is not None:
        self._prune_cq_buffer()
        self._cq_buffer.append(cand)
        self._status(f"PRO jelölt: {call} score={cand.score:.0f} ({cand.reason})")
      return

    self._answer_cq(call, grid, float(report.audio_hz), "alap CQ", report.snr, cycle=report.cycle)

  def _answer_cq(self, call: str, grid: str, audio_hz: float, reason: str, snr: int, *, cycle: str = "") -> None:
    if self._is_outbound_cooldown(call):
      return
    heard = period_from_cycle(cycle) if cycle else ft8_period_at()
    self._begin_qso(call, grid, audio_hz, heard_period=heard)
    self._default_tx_hz = audio_hz
    if self._prefer_fast_report() and grid4_upper(grid):
      rst = snr_report_text(snr)
      self._queue_tx(f"{call} {self._me_callsign} {rst}", audio_hz)
      self._active.rst_sent = rst
      tag = f"{reason}, gyors riport" if reason else "gyors riport"
      self._status(f"CQ → {call} SNR{snr:+d} ({tag}) slot TX={self._active.tx_period}")
      return
    msg = f"{call} {self._me_callsign} {self._my_grid4}"
    self._queue_tx(msg, audio_hz)
    self._status(f"CQ → {call} SNR{snr:+d} ({reason}) slot TX={self._active.tx_period}")

  def _answer_incoming(
    self,
    call: str,
    grid: str,
    audio_hz: float,
    rx_report: str | None,
    snr: int,
    *,
    cycle: str = "",
    skip_our_grid: bool = False,
    reason: str = "",
  ) -> None:
    heard = period_from_cycle(cycle) if cycle else ft8_period_at()
    self._begin_qso(call, grid, audio_hz, heard_period=heard)
    self._default_tx_hz = audio_hz
    me = self._me_callsign
    if rx_report and is_report(rx_report) and not is_r_report(rx_report):
      self._active.rst_rcvd = rst_from_report_token(rx_report)
      rst = snr_report_text(snr)
      if not self._active.rst_sent:
        self._active.rst_sent = rst
      self._queue_tx(f"{call} {me} R{rst}", audio_hz)
      self._status(f"Bejövő → {call} ({rx_report})")
      return
    if skip_our_grid:
      rst = snr_report_text(snr)
      self._queue_tx(f"{call} {me} {rst}", audio_hz)
      self._active.rst_sent = rst
      tag = reason or "grid már CQ-ban"
      self._status(f"CQ → {call} SNR{snr:+d} ({tag})")
      return
    self._queue_tx(f"{call} {me} {self._my_grid4}", audio_hz)
    self._status(f"Bejövő → {call}")

  def _begin_qso(self, remote: str, grid: str, audio_hz: float, *, heard_period: int | None = None) -> None:
    remote = _call_key(remote)
    g = grid4_upper(grid) if grid else self._intel.grid_for(remote)
    tx_p = opposite_period(heard_period) if heard_period is not None else ft8_period_at()
    self._active = ActiveQso(
      remote_call=remote,
      remote_grid=g,
      audio_hz=float(audio_hz),
      time_on=datetime.now(tz=timezone.utc),
      tx_period=tx_p,
      phase=QsoPhase.ACTIVE,
    )
    self._phase = QsoPhase.ACTIVE
    self._cq_wait_remaining = 0

  def _queue_cq(self) -> None:
    if self._active is not None:
      return
    self._phase = QsoPhase.CALLING_CQ
    msg = f"CQ {self._me_callsign} {self._my_grid4}"
    hz = snap_ft8_hz(self._default_tx_hz)
    self._queue_tx(msg, hz)
    self._status(f"CQ @ {hz:.0f} Hz")

  def _resolve_tx_period(self) -> int:
    if self._active is not None:
      return self._active.tx_period
    if self._cq_tx_period is not None:
      return self._cq_tx_period
    return ft8_period_at()

  def _queue_tx(self, message: str, audio_hz: float, *, is_retry: bool = False) -> None:
    audio_hz = snap_ft8_hz(audio_hz)
    period = self._resolve_tx_period()
    slot_id = tx_slot_id(period)
    with self._lock:
      if slot_id == self._queued_slot_id:
        if is_retry:
          return
        self._drain_tx_queue()
      elif not is_retry:
        self._drain_tx_queue()
      if not is_retry:
        self._last_tx_msg = message
        if self._active is not None:
          self._active.cycles_without_reply = 0
      self._queued_slot_id = slot_id
      epoch = self._tx_epoch
    self._on_tx(message)
    self._tx_queue.put((message, audio_hz, period, epoch))

  def _drain_tx_queue(self) -> None:
    self._tx_epoch += 1
    while True:
      try:
        self._tx_queue.get_nowait()
      except queue.Empty:
        break

  def _handle_active_exchange(self, triplet, report: DecodeReport, me: str) -> bool:
    assert self._active is not None
    o = self._active
    third = triplet.third
    if not (is_rr73(third) or is_73(third)) and self._is_self_tx_echo(triplet):
      return True
    # Saját grid TX visszahallása (pl. IK4LZH N0CALL JN96)
    if is_grid_token(third) and third[:4] == self._my_grid4:
      return True
    if not self._triplet_is_active_remote(triplet, me, o.remote_call):
      return False

    if is_grid_token(third):
      o.remote_grid = third[:4]
      if o.rst_sent:
        return True
      self._queue_tx(f"{o.remote_call} {me} {snr_report_text(report.snr)}", o.audio_hz)
      o.rst_sent = snr_report_text(report.snr)
      return True
    if is_report(third) and not is_r_report(third):
      if o.rst_rcvd:
        return True
      o.rst_rcvd = rst_from_report_token(third)
      rst = snr_report_text(report.snr)
      if not o.rst_sent:
        o.rst_sent = rst
      self._queue_tx(f"{o.remote_call} {me} R{rst}", o.audio_hz)
      return True
    if is_r_report(third) or is_rrr(third):
      if self._phase == QsoPhase.CLOSING:
        return True
      self._queue_tx(f"{o.remote_call} {me} RR73", o.audio_hz)
      self._phase = QsoPhase.CLOSING
      return True
    if is_rr73(third) or is_73(third):
      with self._lock:
        self._drain_tx_queue()
        self._queued_slot_id = ""
      self._queue_tx(f"{o.remote_call} {me} 73", o.audio_hz)
      self._finish_qso()
      return True
    return False

  def _resolve_partner_grid(self, call: str, remote_grid: str) -> tuple[str, str]:
    """QSO log — üzenetből, intel, munkamenet- vagy statikus cache."""
    g = grid4_upper(remote_grid)
    if g:
      return g, "message"
    g = self._intel.grid_for(call)
    if g:
      return g, "intel"
    from cw_discover.ft8.grid_geo import lookup as grid_lookup

    cached = grid_lookup.grid_for_call(call)
    if cached:
      return cached, "cache"
    static = self._static_call_grid(call)
    if static:
      return static, "cache"
    return "", "unknown"

  @staticmethod
  def _static_call_grid(call: str) -> str:
    """data/call_grid_cache.json — egyszeri betöltés."""
    if not hasattr(Ft8AutoOperator, "_call_grid_file_cache"):
      path = Path(__file__).resolve().parents[2] / "data" / "call_grid_cache.json"
      cache: dict[str, str] = {}
      try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for k, v in raw.items():
          if isinstance(v, dict) and v.get("grid"):
            cache[_call_key(k)] = grid4_upper(str(v["grid"]))
      except (OSError, json.JSONDecodeError, TypeError):
        pass
      Ft8AutoOperator._call_grid_file_cache = cache
    return Ft8AutoOperator._call_grid_file_cache.get(_call_key(call), "")

  def _finish_qso(self) -> None:
    assert self._active is not None
    o = self._active
    now = datetime.now(tz=timezone.utc)
    grid, grid_source = self._resolve_partner_grid(o.remote_call, o.remote_grid)
    rec = QsoRecord(
      call=o.remote_call,
      grid=grid,
      grid_source=grid_source,
      band=self.band,
      dial_mhz=self.dial_mhz,
      rst_sent=o.rst_sent or "+00",
      rst_rcvd=o.rst_rcvd or "+00",
      time_on=o.time_on,
      time_off=now,
      tx_audio_hz=int(o.audio_hz),
      comment=f"FT8 {self.station.operator_name or self.station.callsign}",
      partner_qth="",
    )
    qid = self.naplo.append_qso(rec)
    self._clear_outbound_cooldown(o.remote_call)
    self._status(f"QSO LOG {o.remote_call} → forgalminaplo ({qid[:8]})")
    self._active = None
    self._phase = QsoPhase.IDLE
    self._cq_wait_remaining = self.station.cq_repeat_cycles

  def _tx_worker(self) -> None:
    while True:
      item = self._tx_queue.get()
      if item is None:
        break
      msg, hz, tx_period, epoch = item

      def should_abort() -> bool:
        with self._lock:
          return epoch != self._tx_epoch

      result = self.tx.transmit(msg, hz, tx_period=tx_period, should_abort=should_abort)
      with self._lock:
        self._queued_slot_id = ""
      if not result.ok:
        self._status(f"TX hiba: {result.error}")

  def _status(self, text: str) -> None:
    self._on_status(text)

  def shutdown(self, *, spin: Callable[[], None] | None = None) -> None:
    """Leállítás: TX megszakítás, worker join (GUI kilépés)."""
    self.halt_transmission()
    self._tx_queue.put(None)
    deadline = time.monotonic() + 8.0
    while self._worker.is_alive() and time.monotonic() < deadline:
      if spin is not None:
        spin()
      self._worker.join(timeout=0.05)
