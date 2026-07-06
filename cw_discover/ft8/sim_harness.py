"""FT8 operátor szimuláció — ál-dekód injektálás, TX rögzítés (nincs rádió)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cw_discover.ft8.engine import DecodeReport
from cw_discover.ft8.forgalmi_log import ForgalmiNaplo
from cw_discover.ft8.ft8_slot import ft8_period_at, period_from_cycle
from cw_discover.ft8.log_replay import LogDecode, cycles_from_base, load_decodes
from cw_discover.ft8.pro_operator import ProOperatorConfig
from cw_discover.ft8.qso_controller import Ft8AutoOperator, QsoPhase
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.ft8.tx_player import TxResult


@dataclass
class SimTx:
  message: str
  audio_hz: float
  tx_period: int | None
  at: float = field(default_factory=time.time)


class RecordingTx:
  """TX mock — nem kulcsol PTT-t, csak rögzít."""

  def __init__(self) -> None:
    self.calls: list[SimTx] = []

  def transmit(self, message: str, audio_hz: float, *, tx_period: int | None = None, **kwargs) -> TxResult:
    self.calls.append(SimTx(message=message, audio_hz=audio_hz, tx_period=tx_period))
    return TxResult(message=message, audio_hz=audio_hz, ok=True)

  def messages(self) -> list[str]:
    return [c.message for c in self.calls]

  def clear(self) -> None:
    self.calls.clear()

  def halt_audio(self) -> None:
    pass

  def force_ptt_off(self) -> None:
    pass


@dataclass
class SimStep:
  label: str
  decode: LogDecode | None = None
  cycle_tick: bool = False
  wait_tx: int | None = None


class Ft8SimHarness:
  """
  Ál-digi csomag injektálás Ft8AutoOperator felé.

  Használat:
    h = Ft8SimHarness()
    h.feed("CQ IK4LZH JN54", cycle="260704_125000", snr=-8, hz=397)
    assert h.last_tx == "IK4LZH N0CALL JN96"
  """

  def __init__(
    self,
    *,
    tmp_dir: Path | None = None,
    callsign: str = "N0CALL",
    grid: str = "JN96",
    pro: ProOperatorConfig | None = None,
    cq_min_snr: int = -20,
  ) -> None:
    pro_cfg = pro or ProOperatorConfig(enabled=False)
    self.station = StationIdentity(
      callsign=callsign,
      grid=grid,
      cq_min_snr=cq_min_snr,
      ptt_port="",
      pro=pro_cfg,
    )
    self.tx = RecordingTx()
    naplo_dir = tmp_dir or Path("/tmp/ft8_sim_naplo")
    self.naplo = ForgalmiNaplo(naplo_dir, station=self.station)
    self.status: list[str] = []
    self.op = Ft8AutoOperator(
      station=self.station,
      naplo=self.naplo,
      tx=self.tx,
      on_status=self.status.append,
    )
    self.op.set_band("40m", 7.074)
    self.op.set_armed(True)

  def feed(
    self,
    message: str,
    *,
    cycle: str | None = None,
    snr: int = -10,
    hz: int = 1500,
    wait: bool = True,
  ) -> None:
    cyc = cycle or self._fresh_cycle()
    before = len(self.tx.calls)
    dec = LogDecode(
      cycle=cyc,
      message=message,
      snr=snr,
      audio_hz=hz,
      rf_khz=7074.0,
      msg_type="",
      time_received=datetime.now(tz=timezone.utc).timestamp(),
    )
    self.op.on_decode(dec.to_report())
    if wait:
      self._wait_tx(before + 1)

  def feed_decode(self, dec: LogDecode, *, wait: bool = True) -> None:
    before = len(self.tx.calls)
    self.op.on_decode(dec.to_report())
    if wait:
      self._wait_tx(before + 1)

  def feed_many(self, decodes: list[LogDecode], *, wait_each: bool = True) -> None:
    for d in decodes:
      self.feed_decode(d, wait=wait_each)

  def tick_cycle(self, cycle: str | None = None) -> None:
    cyc = cycle or self._fresh_cycle()
    self.op.on_cycle(cyc, time.time())

  def run_script(self, steps: list[SimStep]) -> None:
    for step in steps:
      if step.decode is not None:
        self.feed_decode(step.decode, wait=step.wait_tx is not None)
        if step.wait_tx is not None:
          self._wait_tx(step.wait_tx)
      if step.cycle_tick:
        self.tick_cycle(step.decode.cycle if step.decode else None)

  @property
  def last_tx(self) -> str:
    return self.tx.messages()[-1] if self.tx.calls else ""

  @property
  def phase(self) -> QsoPhase:
    return self.op.phase

  @staticmethod
  def _fresh_cycle() -> str:
    t = int(time.time())
    t -= t % 15
    import time as _t

    return _t.strftime("%y%m%d_%H%M%S", _t.gmtime(t))

  def _wait_tx(self, n: int, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and len(self.tx.calls) < n:
      time.sleep(0.01)

  def wait_tx(self, n: int, timeout: float = 1.0) -> None:
    self._wait_tx(n, timeout=timeout)

  def replay_log_file(self, path: Path | str, *, limit: int | None = None) -> list[str]:
    """Napló sorok lejátszása — visszaadja a TX üzeneteket."""
    self.tx.clear()
    decodes = load_decodes(path, limit=limit)
    self.feed_many(decodes, wait_each=True)
    return self.tx.messages()

  def make_cycles(self, base: str, n: int) -> list[str]:
    return cycles_from_base(base, n)
