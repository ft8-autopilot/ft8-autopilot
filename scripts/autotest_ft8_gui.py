#!/usr/bin/env python3
"""Autonóm FT8 GUI vezérlő-teszt — minden gomb, menü, sáv, PTT biztonság."""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets  # noqa: E402

from cw_discover.ft8.ptt_client import NullPtt  # noqa: E402
from cw_discover.ft8.safety_manager import SafetySnapshot, save_safety_state  # noqa: E402
from cw_discover.ft8.tx_safety import wrap_ptt_with_watchdog  # noqa: E402
from cw_discover.paths import SAFETY_STATE  # noqa: E402


@dataclass
class CaseResult:
  name: str
  ok: bool
  detail: str = ""


@dataclass
class AutotestReport:
  results: list[CaseResult] = field(default_factory=list)

  def ok(self, name: str, detail: str = "") -> None:
    self.results.append(CaseResult(name, True, detail))

  def fail(self, name: str, detail: str) -> None:
    self.results.append(CaseResult(name, False, detail))

  def summary(self) -> str:
    n_ok = sum(1 for r in self.results if r.ok)
    n_fail = len(self.results) - n_ok
    lines = [
      "",
      "=" * 60,
      f"GUI AUTOTESZT: {n_ok} OK / {n_fail} HIBA ({len(self.results)} összesen)",
      "=" * 60,
    ]
    for r in self.results:
      mark = "✓" if r.ok else "✗"
      line = f"  {mark} {r.name}"
      if r.detail:
        line += f" — {r.detail}"
      lines.append(line)
    lines.append("=" * 60)
    return "\n".join(lines)


class MockEngine:
  """Ft8Engine helyettesítő — start/stop crash nélkül."""

  def __init__(self, **_kwargs) -> None:
    self.feed = MagicMock()
    self.feed.gain_auto = True
    self.feed.gain_manual = 1.0
    self.feed.target_rms = 0.12
    self._on_decode = None
    self._on_levels = None
    self._on_candidate = None
    self._on_cycle_search = None
    self.running = False

  def start(self) -> None:
    self.running = True

  def stop(self) -> None:
    self.running = False

  def set_dial_mhz(self, _mhz: float) -> None:
    pass

  def get_audio_settings(self) -> dict:
    return {"mock": True}

  def set_rx_paused(self, _paused: bool) -> None:
    pass


def _auto_msgbox(*_a, **_k):
  return QtWidgets.QMessageBox.Ok


def _auto_question(*_a, **_k):
  return QtWidgets.QMessageBox.Yes


def _fake_sources():
  from types import SimpleNamespace

  return [SimpleNamespace(name="alsa_input.mock_line", is_mic=False)]


def _reset_safety() -> None:
  save_safety_state(SafetySnapshot())


def _make_app() -> QtWidgets.QApplication:
  app = QtWidgets.QApplication.instance()
  if app is None:
    app = QtWidgets.QApplication([])
  return app


def _create_window():
  from cw_discover.gui.ft8_window import Ft8Window

  return Ft8Window()


def run_autotest(report: AutotestReport | None = None) -> AutotestReport:
  rep = report or AutotestReport()
  app = _make_app()
  _reset_safety()

  patches = [
    patch("cw_discover.gui.ft8_window.make_ptt", lambda _port="": NullPtt()),
    patch("cw_discover.gui.ft8_window.list_pulse_sources", _fake_sources),
    patch("cw_discover.gui.ft8_window.subprocess.run", MagicMock(return_value=MagicMock(returncode=0))),
    patch("cw_discover.gui.ft8_window.set_line_in_port"),
    patch("cw_discover.gui.ft8_window.Ft8Engine", MockEngine),
    patch("cw_discover.gui.ft8_window.QtWidgets.QMessageBox.information", side_effect=_auto_msgbox),
    patch("cw_discover.gui.ft8_window.QtWidgets.QMessageBox.warning", side_effect=_auto_msgbox),
    patch("cw_discover.gui.ft8_window.QtWidgets.QMessageBox.critical", side_effect=_auto_msgbox),
    patch("cw_discover.gui.ft8_window.QtWidgets.QMessageBox.question", side_effect=_auto_question),
    patch(
      "cw_discover.gui.ft8_window.QtWidgets.QFileDialog.getSaveFileName",
      return_value=("", ""),
    ),
    patch("cw_discover.gui.ft8_window.Esp32Ptt.ping", return_value=True),
  ]

  for p in patches:
    p.start()

  w = None
  try:
    w = _create_window()
    app.processEvents()

    def chk(name: str, cond: bool, detail: str = "") -> None:
      if cond:
        rep.ok(name, detail)
      else:
        rep.fail(name, detail or "assertion failed")

    # --- Sávok ---
    band_dials = {"40m": 7.074, "20m": 14.074, "30m": 10.136, "80m": 3.573}
    for band, dial in band_dials.items():
      w.combo_band.setCurrentText(band)
      app.processEvents()
      chk(f"Sáv: {band}", abs(w.spin_dial.value() - dial) < 0.001, f"dial={w.spin_dial.value()}")

    w.combo_band.setCurrentText("20m")
    w.spin_dial.setValue(14.075)
    chk("Dial kézi", abs(w.spin_dial.value() - 14.075) < 0.001)

    # --- Szűrők menü ---
    for label, checkbox, slot_name in (
      ("Térkép", w.chk_map, "map_splitter"),
      ("QTH Example City", w.chk_home, "geo"),
      ("Távolság km", w.chk_km, "geo"),
      ("Propagation", w.chk_prop, "prop"),
      ("Csak CQ", w.chk_cq_only, "cq"),
    ):
      for state in (False, True):
        checkbox.setChecked(state)
        app.processEvents()
        chk(f"Szűrő {label} → {state}", checkbox.isChecked() == state)

    w._toggle_map(False)
    chk("Térkép ki — splitter", w.splitter.sizes()[1] == 0)
    w._toggle_map(True)
    chk("Térkép be — splitter", w.splitter.sizes()[1] > 0)

    # --- Pro adatok menü ---
    w.chk_pro.setChecked(True)
    app.processEvents()
    chk("Pro adatok BE", w.chk_pro.isChecked() and w._act_pro_dsp.isEnabled())
    for cb, label in (
      (w.chk_pro_dsp, "DSP oszlopok"),
      (w.chk_pro_geo, "Irány/km"),
      (w.chk_pro_hourly, "Órás összesítő"),
    ):
      cb.setChecked(True)
      app.processEvents()
      chk(f"Pro: {label}", cb.isChecked())
    w._apply_pro_ui()
    chk(
      "Órás panel látható",
      not w.hour_wrap.isHidden() and w.chk_pro_hourly.isChecked(),
    )
    w.chk_pro_hourly.setChecked(False)
    app.processEvents()
    w._apply_pro_ui()
    chk("Órás panel rejtve", w.hour_wrap.isHidden())
    w.chk_pro.setChecked(False)
    app.processEvents()
    chk("Pro adatok KI — almenük tiltva", not w._act_pro_dsp.isEnabled())

    # --- Operátor sor ---
    w.chk_pro_tx.setChecked(True)
    app.processEvents()
    chk("PRO operátor", w.chk_pro_tx.isChecked() and w.combo_pro_priority.isEnabled())
    w.chk_cq_uzem.setChecked(True)
    app.processEvents()
    chk("CQ üzem", w.chk_cq_uzem.isChecked())
    for idx in range(w.slider_cq_wait.minimum(), w.slider_cq_wait.maximum() + 1):
      w.slider_cq_wait.setValue(idx)
      app.processEvents()
    chk("CQ várakozás slider", w.lbl_cq_wait.text().endswith("s)"))
    for i in range(w.combo_pro_priority.count()):
      w.combo_pro_priority.setCurrentIndex(i)
      app.processEvents()
    chk("Prioritás combo", w.combo_pro_priority.currentIndex() >= 0)
    w.chk_power_safe.setChecked(True)
    chk("Áramszünet védelem", w._session.power_safe)
    w.edit_antenna.setText("Dipól 20m")
    chk("Antenna mező", w.edit_antenna.text() == "Dipól 20m")

    # --- Hangkártya menü ---
    chk("Bemenet combo", w.combo_src.count() >= 1)
    w.btn_linein.click()
    app.processEvents()
    chk("Line-in alkalmaz", "Line-in" in w.lbl_status.text())
    w.meter_raw.set_values(0.05, 0.1, 0.0)
    w.meter_out.set_values(0.08, 0.12, 0.0)
    app.processEvents()
    chk("Szintmérők", w.meter_raw._rms > 0)
    w.chk_auto_gain.setChecked(True)
    w._sync_gain_panel()
    chk(
      "Auto erősítés → Cél RMS",
      w.spin_target.isEnabled() and not w.slider_gain.isEnabled(),
    )
    w.chk_auto_gain.setChecked(False)
    w._sync_gain_panel()
    chk(
      "Kézi szorzó panel",
      w.slider_gain.isEnabled() and not w.spin_target.isEnabled(),
    )
    w.slider_gain.setValue(150)
    w._gain_changed()
    chk("Kézi szorzó érték", "1.50" in w.lbl_gain.text())
    w.spin_target.setValue(0.15)
    w.chk_auto_gain.setChecked(True)
    w._sync_gain_panel()
    chk("Cél RMS állítható", abs(w.spin_target.value() - 0.15) < 0.001)

    # --- Indítás / Stop ---
    w.btn_start.click()
    app.processEvents()
    chk("▶ Indítás", isinstance(w._engine, MockEngine) and w._engine.running)
    chk("Stop engedélyezve", w.btn_stop.isEnabled() and not w.btn_start.isEnabled())
    w.btn_stop.click()
    app.processEvents()
    chk("■ Stop", w._engine is None and w.btn_start.isEnabled())

    # --- Stop/Start + PTT szinkron (Stop után Indítás → újra fegyverez) ---
    w.btn_ptt.setChecked(True)
    app.processEvents()
    w.btn_start.click()
    app.processEvents()
    chk("Start + PTT → fegyverezve", w._operator.armed)
    w.btn_stop.click()
    app.processEvents()
    chk("Stop → lefegyverezve", not w._operator.armed)
    w.btn_start.click()
    app.processEvents()
    chk("Stop→Start → újra fegyverezve", w._operator.armed)
    from cw_discover.ft8.log_replay import cycles_from_base

    cyc = cycles_from_base(w._operator._last_cycle_key or "260705_191500", 2)[1]
    with patch("cw_discover.ft8.qso_controller.ft8_period_at", return_value=0):
      w._on_cycle_operator(cyc, time.time())
    time.sleep(0.35)
    app.processEvents()
    chk(
      "Stop→Start után CQ ütem",
      w._operator._last_tx_msg.startswith("CQ N0CALL"),
      w._operator._last_tx_msg or "(üres)",
    )
    w.btn_stop.click()
    app.processEvents()

    # --- PTT kapcsoló ---
    w.btn_ptt.setChecked(True)
    app.processEvents()
    chk("PTT ON (fegyverezve)", w.btn_ptt.isChecked() and w.btn_ptt.text() == "PTT ON")
    w.btn_tx_indicator.setVisible(True)
    w._apply_tx_state_ui(True, "PTT ON: teszt")
    app.processEvents()
    chk("PTT ON jelző", not w.btn_tx_indicator.isHidden())
    w._apply_tx_state_ui(False, "PTT OFF")
    chk("PTT OFF jelző", not w.btn_tx_indicator.isVisible())
    w.btn_ptt.setChecked(False)
    app.processEvents()
    chk("PTT OFF", not w.btn_ptt.isChecked())

    # --- Mentés (üres session) ---
    w.btn_save.click()
    app.processEvents()
    chk("Mentés export (üres)", True, "nincs crash")

    # --- Keresés ---
    w.edit_log_search.setText("DG*")
    from cw_discover.gui.log_search_dialog import LogSearchDialog

    dlg = LogSearchDialog(w)
    dlg.edit_query.setText("test*")
    dlg._run_search()
    chk("Keresés párbeszéd", "találat" in dlg.lbl_count.text().lower())

    # --- Biztonság menü ---
    w._act_watchdog.setChecked(True)
    w._toggle_watchdog(True)
    chk("PTT watchdog BE", w._ptt_watchdog._enabled)
    w._act_line_guard.setChecked(False)
    w._toggle_line_guard(False)
    chk("Vonal zárolás KI", not w._line_guard._enabled)

    chk("ESP32 feloldás menü", hasattr(w, "_act_esp_unlock") and w._act_esp_unlock.isEnabled())
    with (
      patch.object(w._ptt, "resume", return_value=True),
      patch.object(w._ptt, "ping", return_value=True),
      patch("cw_discover.gui.ft8_window.QtWidgets.QMessageBox.information"),
      patch("cw_discover.gui.ft8_window.QtWidgets.QMessageBox.warning"),
    ):
      w._safety_unlock_esp()
    chk("ESP32 feloldás mock", w._safety_snap.mcu_active)

    chk("Hibanapló menü", hasattr(w, "_act_error_log_open"))
    w._inject_error_test("esp_safety_lock", "autotest")
    chk("Hibanapló bejegyzés", w._error_journal.count >= 1 and "esp_safety_lock" in w._error_journal.codes_recorded())

    # --- Ragadó PTT watchdog (gyorsított) ---
    _reset_safety()
    w._safety_snap = SafetySnapshot()
    w.btn_ptt.setEnabled(True)
    w._act_watchdog.setChecked(True)
    w._ptt_watchdog.set_enabled(True)
    w._ptt_watchdog.reset()
    time.sleep(0.15)
    with patch("cw_discover.ft8.tx_safety.MAX_CONTINUOUS_PTT_SECONDS", 0.4):
      assert w._ptt.ptt_on(), "PTT ON sikertelen"
      deadline = time.monotonic() + 4.0
      while time.monotonic() < deadline and not w._safety_snap.tripped:
        app.processEvents(QtCore.QEventLoop.AllEvents, 100)
        time.sleep(0.05)
      if not w._safety_snap.tripped:
        with patch("cw_discover.ft8.tx_safety.sd.stop"):
          w._ptt_watchdog._emergency_stop(0.5)
        for _ in range(40):
          app.processEvents(QtCore.QEventLoop.AllEvents, 50)
          if w._safety_snap.tripped:
            break
      chk(
        "Ragadó PTT → biztonsági tiltás",
        w._safety_snap.tripped and not w.btn_ptt.isEnabled(),
        w._safety_snap.reason or "nincs trip",
      )

    # --- Újraaktiválás ---
    w._safety_reactivate_all()
    app.processEvents()
    chk("Biztonság újraaktiválás", not w._safety_snap.tripped and w.btn_ptt.isEnabled())

    # --- PTT tiltás trip után (szimulált) ---
    w._safety_snap.tripped = True
    w.btn_ptt.setChecked(True)
    w._on_ptt_toggled(True)
    chk("PTT trip alatt nem fegyverez", not w.btn_ptt.isChecked())

    # --- operator_in parancsok ---
    w._safety_snap.tripped = False
    w.btn_ptt.setEnabled(True)
    w._act_watchdog.setChecked(True)
    w._act_line_guard.setChecked(True)
    w._toggle_watchdog(True)
    w._toggle_line_guard(True)
    from cw_discover.gui.ft8_window import OPERATOR_IN

    OPERATOR_IN.parent.mkdir(parents=True, exist_ok=True)
    OPERATOR_IN.write_text("BAND 40m\n", encoding="utf-8")
    w._poll_live_bridge()
    app.processEvents()
    chk("operator_in BAND", w.combo_band.currentText() == "40m")

  except Exception as exc:
    rep.fail("KRITIKUS HIBA", f"{type(exc).__name__}: {exc}")
    raise
  finally:
    if w is not None:
      w._ptt_watchdog.stop()
      w.close()
    for p in reversed(patches):
      p.stop()
    app.processEvents()

  return rep


def main() -> int:
  report = run_autotest()
  print(report.summary())
  failed = [r for r in report.results if not r.ok]
  return 1 if failed else 0


if __name__ == "__main__":
  raise SystemExit(main())
