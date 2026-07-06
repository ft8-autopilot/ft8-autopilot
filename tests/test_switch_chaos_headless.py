"""Headless kapcsoló-káosz — PTT, Stop/Start, PRO, prioritás, CQ üzem."""
from __future__ import annotations

import os
import time
from itertools import product
from unittest.mock import patch

import pytest

from cw_discover.ft8.pro_operator import PriorityMode, ProOperatorConfig
from cw_discover.ft8.qso_controller import QsoPhase
from cw_discover.ft8.sim_harness import Ft8SimHarness

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from cw_discover.ft8.log_replay import cycles_from_base


def _fresh_cycles(n: int) -> list[str]:
  h = Ft8SimHarness()
  return cycles_from_base(h._fresh_cycle(), n)


def _tick_cq(h: Ft8SimHarness, cycle: str, *, tx_period: int = 0) -> None:
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=tx_period):
    h.op.on_cycle(cycle, time.time())
  h.wait_tx(len(h.tx.calls) + 1, timeout=2.0)


def test_halt_disarms_rearm_transmits_cq() -> None:
  h = Ft8SimHarness()
  cyc = _fresh_cycles(3)
  _tick_cq(h, cyc[0])
  assert h.last_tx.startswith("CQ N0CALL")
  h.op.halt_transmission("stop")
  assert not h.op.armed
  h.op.set_armed(True)
  assert h.op.armed
  _tick_cq(h, cyc[1])
  assert h.tx.messages()[-1].startswith("CQ N0CALL")


def test_abort_keeps_armed_can_cq_again() -> None:
  h = Ft8SimHarness()
  cyc = _fresh_cycles(4)
  h.feed("CQ IK4LZH JN54", cycle=cyc[0], snr=-8, hz=397, wait=False)
  assert h.op.phase == QsoPhase.ACTIVE
  h.op.abort_qso("teszt")
  assert h.op.phase == QsoPhase.IDLE
  assert h.op.armed
  _tick_cq(h, cyc[1])
  assert h.tx.messages()[-1].startswith("CQ N0CALL")


@pytest.mark.parametrize(
  "priority",
  [
    PriorityMode.BALANCED,
    PriorityMode.DISTANCE,
    PriorityMode.WEAK_DX,
    PriorityMode.STRONG_FAST,
  ],
)
def test_pro_priority_cq_answer_then_halt_rearm(priority: PriorityMode) -> None:
  pro = ProOperatorConfig(enabled=True, priority=priority, defer_cq_pick=False, max_snr=10)
  h = Ft8SimHarness(pro=pro)
  cyc = _fresh_cycles(5)
  h.feed("CQ IK4LZH JN54", cycle=cyc[0], snr=-8, hz=397)
  assert h.op._active is not None
  first = h.last_tx
  assert "IK4LZH N0CALL" in first
  if priority == PriorityMode.STRONG_FAST:
    assert "JN96" not in first
    assert "-08" in first or "-09" in first or first.endswith(("-08", "-09", "-10"))
  else:
    assert first.endswith("JN96")
  h.op.halt_transmission("váltás")
  h.op.set_armed(True)
  _tick_cq(h, cyc[2])
  assert h.tx.messages()[-1].startswith("CQ N0CALL")


def test_cq_only_incoming_vs_pro_toggle() -> None:
  h = Ft8SimHarness(pro=ProOperatorConfig(enabled=True, defer_cq_pick=True))
  cyc = _fresh_cycles(3)
  h.op.set_cq_only_mode(True)
  h.feed("CQ FO1AAA JN28", cycle=cyc[0], snr=-5, wait=False)
  assert h.op.phase == QsoPhase.IDLE
  h.feed("FO1AAA N0CALL JN28", cycle=cyc[0], snr=-6, hz=900, wait=False)
  with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=1):
    h.op.on_cycle(cyc[1], time.time())
  h.wait_tx(1, timeout=2.0)
  assert "FO1AAA N0CALL" in h.last_tx
  h.op.set_cq_only_mode(False)
  h.op.set_pro_config(ProOperatorConfig(enabled=False))
  h.op.halt_transmission("")
  h.op.set_armed(True)
  _tick_cq(h, cyc[2])
  assert h.last_tx.startswith("CQ N0CALL")


def test_toggle_storm_invariants() -> None:
  """Véletlenszerű kapcsoló-zár — invariánsok: halt→disarm, fegyverezve→CQ."""
  pro_modes = [True, False]
  cq_only = [True, False]
  priorities = list(PriorityMode)
  h = Ft8SimHarness()
  cyc = _fresh_cycles(40)
  ci = 0
  for pro_on, cq_on, pr in product(pro_modes, cq_only, priorities):
    cfg = ProOperatorConfig(enabled=pro_on, priority=pr, defer_cq_pick=pro_on)
    h.op.set_pro_config(cfg)
    h.op.set_cq_only_mode(cq_on)
    h.op.halt_transmission("storm")
    assert not h.op.armed
    h.op.set_armed(True)
    assert h.op.armed
    if ci < len(cyc):
      with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
        h.op.on_cycle(cyc[ci], time.time())
      ci += 1
      if h.op.phase == QsoPhase.IDLE and h.op.armed:
        time.sleep(0.05)
        assert len(h.tx.calls) >= 1


def test_gui_stop_start_ptt_rearm_offscreen() -> None:
  from unittest.mock import MagicMock

  from PyQt5 import QtWidgets

  from cw_discover.ft8.ptt_client import NullPtt
  from cw_discover.gui.ft8_window import Ft8Window

  app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
  with (
    patch("cw_discover.gui.ft8_window.make_ptt", lambda _port="": NullPtt()),
    patch("cw_discover.gui.ft8_window.list_pulse_sources", lambda: []),
    patch("cw_discover.gui.ft8_window.subprocess.run", MagicMock(return_value=MagicMock(returncode=0))),
    patch("cw_discover.gui.ft8_window.set_line_in_port"),
    patch("cw_discover.gui.ft8_window.Ft8Engine") as MockEng,
    patch("cw_discover.gui.ft8_window.Esp32Ptt.ping", return_value=True),
  ):
    eng_inst = MagicMock()
    eng_inst.feed = MagicMock()
    eng_inst.feed.gain_auto = True
    eng_inst.feed.gain_manual = 1.0
    eng_inst.feed.target_rms = 0.12
    eng_inst.running = False

    def _start_eng():
      eng_inst.running = True

    def _stop_eng():
      eng_inst.running = False

    eng_inst.start = _start_eng
    eng_inst.stop = _stop_eng
    eng_inst.get_audio_settings = lambda: {}
    MockEng.return_value = eng_inst

    w = Ft8Window()
    app.processEvents()
    w.btn_ptt.setChecked(True)
    app.processEvents()
    w.btn_start.click()
    app.processEvents()
    assert w._operator.armed, "Start után PTT be → operátor fegyverezve"
    w.btn_stop.click()
    app.processEvents()
    assert not w._operator.armed, "Stop → halt lefegyverez"
    assert w.btn_ptt.isChecked(), "PTT gomb maradhat be"
    w.btn_start.click()
    app.processEvents()
    assert w._operator.armed, "Stop→Start után újra fegyverezve"
    cyc = _fresh_cycles(1)[0]
    with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
      w._on_cycle_operator(cyc, time.time())
    time.sleep(0.3)
    app.processEvents()
    assert w._operator._last_tx_msg.startswith("CQ N0CALL"), (
      f"várható CQ, kapott: {w._operator._last_tx_msg!r}"
    )
    w._ptt_watchdog.stop()
    w.close()
    app.processEvents()
