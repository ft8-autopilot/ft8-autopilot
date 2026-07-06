"""TX azonnali leállítás — Stop / kilépés / epoch megszakítás."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np

from cw_discover.ft8.qso_controller import Ft8AutoOperator
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.ft8.tx_player import Ft8TxPlayer


def test_halt_transmission_drains_and_ptt_off() -> None:
  ptt = MagicMock()
  ptt.ptt_off.return_value = True
  tx = Ft8TxPlayer(ptt=ptt, simulate=True)
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port=None)
  op = Ft8AutoOperator(station=st, tx=tx)
  op.set_armed(True)
  op._queue_tx("CQ N0CALL JN96", 1500.0)
  op.halt_transmission("teszt")
  assert not op.armed
  assert op._active is None
  assert ptt.ptt_off.call_count >= 1


def test_rearm_after_halt_resets_cq_scheduler() -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port=None)
  op = Ft8AutoOperator(station=st, tx=Ft8TxPlayer(simulate=True))
  op.set_armed(True)
  op._last_cycle_key = "2026-07-05T19:00:00"
  op._cq_tx_period = 1
  op.halt_transmission("stop")
  assert not op.armed
  op.set_armed(True)
  assert op.armed
  assert op._last_cycle_key == ""
  assert op._cq_tx_period is None


def test_transmit_aborts_on_epoch_during_play() -> None:
  ptt = MagicMock()
  ptt.ptt_on.return_value = True
  ptt.ptt_off.return_value = True
  tx = Ft8TxPlayer(ptt=ptt, simulate=False)
  wave = np.zeros(12_000 * 3, dtype=np.float32)  # ~3 s
  abort = threading.Event()

  def should_abort() -> bool:
    return abort.is_set()

  with (
    patch.object(tx, "build_wave", return_value=wave),
    patch.object(tx, "wait_for_tx_slot"),
    patch.object(tx, "_ensure_line_out"),
    patch.object(tx, "_pulse_sink_env"),
    patch("cw_discover.ft8.tx_player.sd.play"),
    patch("cw_discover.ft8.tx_player.sd.get_stream", return_value=MagicMock(active=True)),
    patch("cw_discover.ft8.tx_player.sd.stop"),
  ):
    result_holder: list = []

    def run() -> None:
      result_holder.append(
        tx.transmit("CQ N0CALL JN96", 1500.0, should_abort=should_abort)
      )

    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.08)
    abort.set()
    tx.halt_audio()
    th.join(timeout=5.0)
    assert not th.is_alive()
    assert result_holder
    assert result_holder[0].error == "aborted"
    assert ptt.ptt_off.call_count >= 1


def test_operator_shutdown_calls_halt() -> None:
  ptt = MagicMock()
  tx = Ft8TxPlayer(ptt=ptt, simulate=True)
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port=None)
  op = Ft8AutoOperator(station=st, tx=tx)
  with patch.object(op, "halt_transmission") as mock_halt:
    op.shutdown()
    mock_halt.assert_called_once()
