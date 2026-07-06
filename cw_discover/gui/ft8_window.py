"""FT8 élő dekóder GUI — PyFT8 LDPC + line-in."""
from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from cw_discover.audio.pulse_sources import list_pulse_sources
from cw_discover.ft8.audio_feed import set_line_in_port
from cw_discover.ft8.engine import DecodeReport, Ft8Engine, DEFAULT_LINEIN
from cw_discover.ft8.json_fast import dumps_compact
from cw_discover.ft8.decode_meta import message_preamble, message_preamble_geo, message_upper, time_hms_utc, time_iso_utc
from cw_discover.ft8.grid_geo import _call_key, extract_callsigns_from_message, lookup as grid_lookup
from cw_discover.ft8.home_qth import DEFAULT_HOME, HomeQth
from cw_discover.ft8.pro_operator import PriorityMode
from cw_discover.ft8.ptt_client import Esp32Ptt, make_ptt
from cw_discover.ft8.qso_controller import Ft8AutoOperator, QsoPhase
from cw_discover.ft8.safety_manager import (
  load_safety_state,
  mark_reactivated,
  mark_tripped,
  save_safety_state,
  status_summary,
)
from cw_discover.ft8.session_log import LOG_DIR, SessionLog
from cw_discover.ft8.station_identity import CQ_WAIT_PERIOD_CHOICES, StationIdentity
from cw_discover.ft8.tx_player import Ft8TxPlayer
from cw_discover.ft8.tx_safety import LineOutGuard, wrap_ptt_with_watchdog
from cw_discover.ft8.log_search import today_log_day
from cw_discover.gui.log_search_dialog import LogSearchDialog
from cw_discover.gui.error_journal import (
  ErrorJournal,
  MAX_ENTRIES,
  bind_error_journal,
  report_error,
  report_tx_error,
)
from cw_discover.gui.error_log_dialog import ErrorLogDialog
from cw_discover.gui.error_catalog import ALL_CODES
from cw_discover.gui.world_map_widget import WorldMapWidget

from cw_discover.paths import ERROR_JOURNAL, FORGALMI_LIVE, STATION_FILE
OPERATOR_IN = FORGALMI_LIVE / "operator_in.txt"
GUI_LIVE_STATUS = FORGALMI_LIVE / "gui_status.json"


class LevelMeter(QtWidgets.QWidget):
  def __init__(self, title: str, color: str, parent=None) -> None:
    super().__init__(parent)
    self._title = title
    self._color = QtGui.QColor(color)
    self._rms = 0.0
    self._peak = 0.0
    self._clip = 0.0
    self.setMinimumHeight(64)

  def set_values(self, rms: float, peak: float, clip: float) -> None:
    self._rms = rms
    self._peak = peak
    self._clip = clip
    self.update()

  def paintEvent(self, _event) -> None:
    p = QtGui.QPainter(self)
    w, h = self.width(), self.height()
    p.fillRect(0, 0, w, h, QtGui.QColor("#161b22"))
    p.setPen(QtGui.QColor("#8b949e"))
    p.drawText(8, 14, self._title)
    bar_y, bar_h = 20, h - 34
    p.setBrush(QtGui.QColor("#21262d"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawRoundedRect(8, bar_y, w - 16, bar_h, 3, 3)
    db = 20.0 * np.log10(max(self._rms, 1e-8))
    frac = float(np.clip((db + 50.0) / 50.0, 0.0, 1.0))
    col = QtGui.QColor("#f85149") if self._clip > 0.01 else self._color
    fill_w = int((w - 16) * frac)
    if fill_w > 0:
      p.setBrush(col)
      p.drawRoundedRect(8, bar_y, fill_w, bar_h, 3, 3)
    p.setPen(QtGui.QColor("#c9d1d9"))
    clip_txt = f" CLIP {100*self._clip:.0f}%" if self._clip > 0.005 else ""
    p.drawText(8, h - 8, f"RMS {self._rms:.4f}  peak {self._peak:.3f}{clip_txt}")
    p.end()


class Ft8Window(QtWidgets.QMainWindow):
  decode_signal = QtCore.pyqtSignal(object)
  levels_signal = QtCore.pyqtSignal(float, float, float, float, float)
  geo_signal = QtCore.pyqtSignal(int, str)  # decode_id, hely szöveg
  cycle_signal = QtCore.pyqtSignal()
  cycle_op_signal = QtCore.pyqtSignal(str)  # cycle → UI szál (vevő threadből)
  _CYCLE_OP_DELAY_MS = 1200  # késő dekódok a slot elején (PyFT8 demap)

  _PRO_COL = 7
  _N_COLS = 12
  _BG_QSO_ACTIVE = QtGui.QColor("#3d2e00")
  _BG_QSO_DONE = QtGui.QColor("#0f2d1a")
  _BG_QSO_COOLDOWN = QtGui.QColor("#1c2128")
  _FG_QSO_COOLDOWN = QtGui.QColor("#6e7681")

  def __init__(self) -> None:
    super().__init__()
    self.setWindowTitle("FT8 vétel — FT-817 / line-in (PyFT8 LDPC)")
    self.resize(1180, 640)
    self._engine: Ft8Engine | None = None
    self._decode_count = 0
    self._decode_seq = 0
    self._decode_geo: dict[int, dict] = {}
    self._decode_calls: dict[int, list[str]] = {}
    self._last_cycle_for_audio = ""
    self._qso_active_call = ""
    self._qso_completed_calls: set[str] = set()
    self._worked_utc_day = ""
    self._session = SessionLog()
    self._error_journal = ErrorJournal(ERROR_JOURNAL)
    bind_error_journal(self._error_journal)
    self._rx_stall_last_decode = 0
    self._rx_stall_since_mono = time.monotonic()
    self._rx_stall_reported = False
    self._station = StationIdentity.load()
    self._safety_snap = load_safety_state()
    tripped = self._safety_snap.tripped
    self._line_guard = LineOutGuard()
    self._line_guard.set_enabled(self._safety_snap.line_guard_on and not tripped)
    if not tripped and self._safety_snap.line_guard_on:
      self._line_guard.acquire()
    raw_ptt = make_ptt(self._station.ptt_port)
    self._ptt, self._ptt_watchdog = wrap_ptt_with_watchdog(
      raw_ptt,
      enabled=bool(self._station.ptt_port) and self._safety_snap.watchdog_on and not tripped,
      on_emergency=self._on_ptt_emergency,
    )
    if not tripped and self._safety_snap.watchdog_on:
      self._ptt_watchdog.start()
    self._ptt.sync_time()
    self._tx_active = False
    self._last_tx_error = ""
    self._last_gui_status_mono = 0.0
    self._last_gui_status_phase = ""
    self._last_gui_status_partner = ""
    self._hourly_refresh_mono = 0.0
    self._map_refresh_mono = 0.0
    self._highlight_refresh_mono = 0.0
    self._footer_refresh_mono = 0.0
    self._operator_in_mtime = -1.0
    self._ptt_ok = True
    self._shutting_down = False
    inner_ptt = getattr(self._ptt, "_inner", self._ptt)
    if isinstance(inner_ptt, Esp32Ptt):
      self._ptt_ok = self._ptt.ping()
    self._operator = Ft8AutoOperator(
      station=self._station,
      tx=Ft8TxPlayer(
        ptt=self._ptt,
        audio_device=self._station.tx_audio_device,
        on_state=self._on_tx_state,
        line_guard=self._line_guard,
      ),
      on_status=self._on_operator_status,
      on_tx=self._on_operator_tx,
    )

    self.decode_signal.connect(self._on_decode_ui)
    self.levels_signal.connect(self._on_levels_ui)
    self.geo_signal.connect(self._on_geo_ui, QtCore.Qt.QueuedConnection)
    self.cycle_signal.connect(self._refresh_hourly_table, QtCore.Qt.QueuedConnection)
    self.cycle_op_signal.connect(self._on_cycle_operator_delayed, QtCore.Qt.QueuedConnection)
    threading.Thread(target=grid_lookup.ensure_ready, daemon=True).start()

    root = QtWidgets.QWidget()
    self.setCentralWidget(root)
    layout = QtWidgets.QVBoxLayout(root)

    # --- felső sor: sáv, indítás, keresés ---
    row1 = QtWidgets.QHBoxLayout()
    row1.addWidget(QtWidgets.QLabel("Sáv:"))
    self.combo_band = QtWidgets.QComboBox()
    self.combo_band.addItems(["40m", "20m", "30m", "80m"])
    row1.addWidget(self.combo_band)

    row1.addWidget(QtWidgets.QLabel("Dial (MHz):"))
    self.spin_dial = QtWidgets.QDoubleSpinBox()
    self.spin_dial.setRange(1.8, 50.0)
    self.spin_dial.setDecimals(3)
    self.spin_dial.setSingleStep(0.001)
    self.spin_dial.setValue(7.074)
    row1.addWidget(self.spin_dial)

    row1.addStretch(1)

    self.edit_log_search = QtWidgets.QLineEdit()
    self.edit_log_search.setPlaceholderText("Keresés mai naplóban… (? *)")
    self.edit_log_search.setClearButtonEnabled(True)
    self.edit_log_search.setMaximumWidth(220)
    self.edit_log_search.setToolTip(
      "Enter vagy 🔍 — decodes, QSO, naplo.txt; ? egy karakter, * tetszőleges"
    )
    self.btn_log_search = QtWidgets.QPushButton("🔍")
    self.btn_log_search.setFixedWidth(36)
    self.btn_log_search.setToolTip("Keresés a mai logfájlokban")
    row1.addWidget(self.edit_log_search)
    row1.addWidget(self.btn_log_search)

    self.btn_save = QtWidgets.QPushButton("Mentés export")
    self.btn_save.setToolTip("Munkamenet export: JSON + JSONL + órás/ciklus + ADIF (+ Parquet ha telepítve)")
    row1.addWidget(self.btn_save)

    self.btn_ptt = QtWidgets.QPushButton("PTT")
    self.btn_ptt.setCheckable(True)
    self.btn_ptt.setToolTip(
      f"Adás: {self._station.callsign} ({self._station.operator_name}), "
      f"QTH {self._station.qth} — forgalminaplo/"
    )
    self._style_ptt_button(False)
    self.btn_start = QtWidgets.QPushButton("▶ Indítás")
    self.btn_stop = QtWidgets.QPushButton("■ Stop")
    self.btn_stop.setEnabled(False)
    row1.addWidget(self.btn_ptt)
    row1.addWidget(self.btn_start)
    row1.addWidget(self.btn_stop)
    layout.addLayout(row1)

    # --- szűrők (menüsáv, rejtett checkboxok) ---
    self.chk_map = QtWidgets.QCheckBox()
    self.chk_map.setChecked(True)
    self.chk_map.hide()
    self.chk_home = QtWidgets.QCheckBox()
    self.chk_home.setChecked(True)
    self.chk_home.hide()
    self.chk_km = QtWidgets.QCheckBox()
    self.chk_km.setChecked(True)
    self.chk_km.hide()
    self.chk_prop = QtWidgets.QCheckBox()
    self.chk_prop.setChecked(True)
    self.chk_prop.hide()
    self.chk_cq_only = QtWidgets.QCheckBox()
    self.chk_cq_only.hide()

    # --- operátor sor (elöl maradó vezérlők) ---
    op_row = QtWidgets.QHBoxLayout()
    self.chk_pro = QtWidgets.QCheckBox()
    self.chk_pro.hide()
    self.chk_pro_dsp = QtWidgets.QCheckBox()
    self.chk_pro_dsp.hide()
    self.chk_pro_geo = QtWidgets.QCheckBox()
    self.chk_pro_geo.hide()
    self.chk_pro_hourly = QtWidgets.QCheckBox()
    self.chk_pro_hourly.hide()

    self.chk_pro_tx = QtWidgets.QCheckBox("PRO operátor")
    self.chk_pro_tx.setToolTip(
      "Intelligens CQ rangsorolás: gyenge/messzi állomás preferálása, SNR ablak, "
      "ciklus-végén legjobb jelölt. Meta: data/FT8_PRO_OPERATOR_META.md"
    )
    self.chk_pro_tx.setChecked(self._station.pro.enabled)
    self.chk_cq_uzem = QtWidgets.QCheckBox("CQ üzem")
    self.chk_cq_uzem.setToolTip(
      "Csak saját CQ + rád hívók + rád hívók. Idegen CQ-ra nem válaszol. "
      "Több hívó esetén PRO pontozás; grid már a CQ-ban — reporttal folytat."
    )
    self.chk_cq_uzem.setChecked(False)
    op_row.addWidget(self.chk_pro_tx)
    op_row.addWidget(self.chk_cq_uzem)
    op_row.addWidget(QtWidgets.QLabel("CQ várakozás:"))
    self.slider_cq_wait = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.slider_cq_wait.setRange(0, len(CQ_WAIT_PERIOD_CHOICES) - 1)
    self.slider_cq_wait.setValue(self._cq_wait_slider_index(self._station.cq_repeat_cycles))
    self.slider_cq_wait.setFixedWidth(100)
    self.slider_cq_wait.setToolTip(
      "CQ adás után ennyi 15 mp-es periódus hallgatás (1/3/5/7/9), majd újra CQ vagy válasz hívónak"
    )
    op_row.addWidget(self.slider_cq_wait)
    self.lbl_cq_wait = QtWidgets.QLabel(self._cq_wait_label())
    self.lbl_cq_wait.setMinimumWidth(88)
    op_row.addWidget(self.lbl_cq_wait)
    self.combo_pro_priority = QtWidgets.QComboBox()
    self.combo_pro_priority.addItems(
      ["Kiegyensúlyozott", "Távolság (DX)", "Gyenge=DX", "Erős (gyors)"]
    )
    self.combo_pro_priority.setEnabled(self.chk_pro_tx.isChecked())
    self.combo_pro_priority.setToolTip("CQ válasz prioritás — csak PRO operátor ON")
    self.chk_power_safe = QtWidgets.QCheckBox("Áramszünet védelem")
    self.chk_power_safe.setToolTip(
      "Napi log + atomi session_snapshot.json — fsync minden 5 perces mentésnél (Stop is ment)"
    )
    op_row.addWidget(self.combo_pro_priority)
    op_row.addWidget(self.chk_power_safe)
    op_row.addStretch(1)
    self.edit_antenna = QtWidgets.QLineEdit()
    self.edit_antenna.setPlaceholderText("Antenna jegyzet (exportba, opcionális)")
    self.edit_antenna.setMaximumWidth(320)
    op_row.addWidget(self.edit_antenna)
    layout.addLayout(op_row)

    # --- hangkártya (menüpanel, nem fő sor) ---
    self.combo_src = QtWidgets.QComboBox()
    self.btn_linein = QtWidgets.QPushButton("Line-in port alkalmaz")
    self.chk_auto_gain = QtWidgets.QCheckBox("Auto erősítés")
    self.chk_auto_gain.setChecked(True)
    self.slider_gain = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.slider_gain.setRange(5, 300)
    self.slider_gain.setValue(100)
    self.slider_gain.setToolTip("0.05× … 3.0×")
    self.lbl_gain = QtWidgets.QLabel("1.00×")
    self.lbl_gain.setMinimumWidth(44)
    self.spin_target = QtWidgets.QDoubleSpinBox()
    self.spin_target.setRange(0.02, 0.5)
    self.spin_target.setSingleStep(0.01)
    self.spin_target.setValue(0.12)
    self._gain_auto_wrap = QtWidgets.QWidget()
    gain_auto_lay = QtWidgets.QHBoxLayout(self._gain_auto_wrap)
    gain_auto_lay.setContentsMargins(16, 0, 0, 0)
    gain_auto_lay.addWidget(QtWidgets.QLabel("Cél RMS:"))
    gain_auto_lay.addWidget(self.spin_target)
    self._gain_manual_wrap = QtWidgets.QWidget()
    gain_manual_lay = QtWidgets.QHBoxLayout(self._gain_manual_wrap)
    gain_manual_lay.setContentsMargins(16, 0, 0, 0)
    gain_manual_lay.addWidget(QtWidgets.QLabel("Kézi szorzó:"))
    gain_manual_lay.addWidget(self.slider_gain, stretch=1)
    gain_manual_lay.addWidget(self.lbl_gain)

    self.meter_raw = LevelMeter("Nyers line-in", "#58a6ff")
    self.meter_out = LevelMeter("Dekóder bemenet", "#3fb950")

    self.lbl_status = QtWidgets.QLabel(
      "PyFT8 LDPC dekóder (WSJT-X kompatibilis paritásmátrix). "
      "USB, 7074 kHz dial — FT8 jelek ~500–2800 Hz audio sávban."
    )
    self.lbl_status.setWordWrap(True)
    self.btn_tx_indicator = QtWidgets.QPushButton("PTT ON")
    self.btn_tx_indicator.setVisible(False)
    self.btn_tx_indicator.setEnabled(False)
    self.btn_tx_indicator.setFixedHeight(30)
    self._style_tx_indicator(False)
    status_row = QtWidgets.QHBoxLayout()
    status_row.addWidget(self.btn_tx_indicator)
    status_row.addWidget(self.lbl_status, stretch=1)
    layout.addLayout(status_row)

    self.lbl_qso = QtWidgets.QLabel("QSO: nincs aktív kapcsolat")
    self.lbl_qso.setAlignment(QtCore.Qt.AlignCenter)
    self.lbl_qso.setMinimumHeight(38)
    self._style_qso_banner_idle()
    layout.addWidget(self.lbl_qso)

    self._qso_banner_timer = QtCore.QTimer(self)
    self._qso_banner_timer.setSingleShot(True)
    self._qso_banner_timer.timeout.connect(self._refresh_qso_banner)

    self.splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    table_wrap = QtWidgets.QWidget()
    table_layout = QtWidgets.QVBoxLayout(table_wrap)
    table_layout.setContentsMargins(0, 0, 0, 0)

    # --- dekód táblázat ---
    self.table = QtWidgets.QTableWidget(0, self._N_COLS)
    self.table.setHorizontalHeaderLabels(
      [
        "Idő",
        "Ciklus",
        "SNR",
        "Δt",
        "Audio Hz",
        "Üzenet",
        "Hely (lokátor)",
        "Típus",
        "Sync",
        "ncheck",
        "Irány°",
        "km",
      ]
    )
    hdr = self.table.horizontalHeader()
    hdr.setStretchLastSection(True)
    hdr.setSectionResizeMode(5, QtWidgets.QHeaderView.Stretch)
    hdr.setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
    self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    self.table.setAlternatingRowColors(True)
    self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    self.table.setSortingEnabled(False)
    table_layout.addWidget(self.table)

    self.hour_wrap = QtWidgets.QWidget()
    hour_layout = QtWidgets.QVBoxLayout(self.hour_wrap)
    hour_layout.setContentsMargins(0, 4, 0, 0)
    hour_layout.addWidget(QtWidgets.QLabel("Órás összesítő (UTC) — dekódok / egyedi hívó / irány eloszlás"))
    self.hour_table = QtWidgets.QTableWidget(0, 8)
    self.hour_table.setHorizontalHeaderLabels(
      [
        "Óra UTC",
        "Dekód",
        "Hívójel",
        "Új áll.",
        "SNR átl",
        "Térkép",
        "Irány",
        "Clip",
      ]
    )
    self.hour_table.horizontalHeader().setStretchLastSection(True)
    self.hour_table.setMaximumHeight(120)
    self.hour_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    hour_layout.addWidget(self.hour_table)
    self.hour_wrap.setVisible(False)
    table_layout.addWidget(self.hour_wrap)

    self.splitter.addWidget(table_wrap)

    self.map_widget = WorldMapWidget()
    self.splitter.addWidget(self.map_widget)
    self.splitter.setStretchFactor(0, 3)
    self.splitter.setStretchFactor(1, 2)

    self._prop_timer = QtCore.QTimer(self)
    self._prop_timer.setInterval(2000)
    self._prop_timer.timeout.connect(self._tick_propagation_overlay)
    layout.addWidget(self.splitter, stretch=1)

    self.lbl_footer = QtWidgets.QLabel("Dekódok: 0 | Hallott állomások: 0 (0 térképen)")
    layout.addWidget(self.lbl_footer)

    self.btn_linein.clicked.connect(self._apply_linein)
    self.btn_start.clicked.connect(self._start)
    self.btn_stop.clicked.connect(self._stop)
    self.btn_ptt.toggled.connect(self._on_ptt_toggled)
    self.btn_save.clicked.connect(self._save_session)
    self.btn_log_search.clicked.connect(self._open_log_search)
    self.edit_log_search.returnPressed.connect(self._open_log_search)
    self.chk_map.toggled.connect(self._toggle_map)
    self.chk_home.toggled.connect(self._apply_geo_display)
    self.chk_km.toggled.connect(self._apply_geo_display)
    self.chk_prop.toggled.connect(self._on_prop_overlay_toggled)
    self.chk_cq_only.toggled.connect(self._on_cq_only_toggled)
    self.chk_auto_gain.toggled.connect(self._sync_gain_panel)
    self.chk_auto_gain.toggled.connect(self._gain_changed)
    self.slider_gain.valueChanged.connect(self._gain_changed)
    self.spin_target.valueChanged.connect(self._gain_changed)
    self.spin_dial.valueChanged.connect(self._dial_changed)
    self.combo_band.currentTextChanged.connect(self._band_changed)
    self.chk_pro.toggled.connect(self._apply_pro_ui)
    self.chk_pro_dsp.toggled.connect(self._apply_pro_columns)
    self.chk_pro_geo.toggled.connect(self._apply_pro_columns)
    self.chk_pro_hourly.toggled.connect(self._apply_pro_ui)
    self.chk_pro_tx.toggled.connect(self._on_pro_tx_toggled)
    self.chk_cq_uzem.toggled.connect(self._on_cq_uzem_toggled)
    self.slider_cq_wait.valueChanged.connect(self._on_cq_wait_changed)
    self.combo_pro_priority.currentIndexChanged.connect(self._on_pro_priority_changed)
    self.chk_power_safe.toggled.connect(self._on_power_safe_toggled)

    self._fill_sources()
    self._band_changed(self.combo_band.currentText())
    self._reload_worked_qso_highlights()
    self._toggle_map(self.chk_map.isChecked())
    self._apply_geo_display()
    self._apply_pro_columns()
    self._sync_pro_priority_combo()
    if not self._ptt_ok:
      err = getattr(self._ptt, "last_error", "ESP32 /dev/ttyUSB0 nem válaszol")
      self.lbl_status.setText(f"⚠ PTT hiba: {err}")
      self._log_gui_error("esp_ping_fail", err, dedup=False)
    inner_ptt = getattr(self._ptt, "_inner", self._ptt)
    if isinstance(inner_ptt, Esp32Ptt) and inner_ptt.status().get("lock"):
      if not self._resume_esp_after_lock("induláskor LOCK=1"):
        self._log_gui_error(
          "esp_resume_fail",
          getattr(self._ptt, "last_error", ""),
          dedup=False,
        )

    self._build_safety_menu()
    self._build_filter_menu()
    self._build_pro_menu()
    self._build_audio_menu()
    self._build_error_log_menu()
    self._sync_gain_panel()
    self._apply_pro_ui()
    self._update_safety_menu()
    if tripped:
      self.btn_ptt.setEnabled(False)
      self.btn_ptt.setChecked(False)
      self.lbl_status.setText(f"⚠ Biztonsági tiltás aktív — {self._safety_snap.reason}")
      self._log_gui_error("safety_startup_tripped", self._safety_snap.reason, dedup=False)
      QtCore.QTimer.singleShot(400, self._show_safety_trip_banner)

    self._live_timer = QtCore.QTimer(self)
    self._live_timer.timeout.connect(self._poll_live_bridge)
    self._live_timer.start(1000)
    FORGALMI_LIVE.mkdir(parents=True, exist_ok=True)

  def _build_safety_menu(self) -> None:
    menu = self.menuBar().addMenu("Biztonság")

    self._act_safety_status = QtWidgets.QAction("Állapot", self)
    self._act_safety_status.setEnabled(False)
    menu.addAction(self._act_safety_status)
    menu.addSeparator()

    self._act_watchdog = QtWidgets.QAction("PTT watchdog (20 s)", self)
    self._act_watchdog.setCheckable(True)
    self._act_watchdog.setChecked(self._safety_snap.watchdog_on)
    self._act_watchdog.setToolTip("Ragadó adás esetén azonnali leállítás + ESP tiltás")
    self._act_watchdog.triggered.connect(self._toggle_watchdog)
    menu.addAction(self._act_watchdog)

    self._act_line_guard = QtWidgets.QAction("Vonalkimenet zárolás", self)
    self._act_line_guard.setCheckable(True)
    self._act_line_guard.setChecked(self._safety_snap.line_guard_on)
    self._act_line_guard.setToolTip("Más programok ne használják a vonal kimenetet (rádió audio)")
    self._act_line_guard.triggered.connect(self._toggle_line_guard)
    menu.addAction(self._act_line_guard)

    menu.addSeparator()

    self._act_mcu_shutdown = QtWidgets.QAction("ESP32 leállítás (SHUTDOWN)", self)
    self._act_mcu_shutdown.setToolTip("PTT OFF + mikrokontroller biztonsági tiltás + soros bontás")
    self._act_mcu_shutdown.triggered.connect(self._safety_shutdown_mcu)
    menu.addAction(self._act_mcu_shutdown)

    self._act_esp_unlock = QtWidgets.QAction("ESP32 feloldás (RESUME)", self)
    self._act_esp_unlock.setToolTip(
      "ESP32 SAFETY_LOCK tiltás feloldása — TX hiba: ERR SAFETY_LOCK esetén"
    )
    self._act_esp_unlock.triggered.connect(self._safety_unlock_esp)
    menu.addAction(self._act_esp_unlock)

    self._act_reactivate = QtWidgets.QAction("Összes újraaktiválás", self)
    self._act_reactivate.setToolTip("ESP RESUME, vonal zárolás, watchdog — tiltás feloldása")
    self._act_reactivate.triggered.connect(self._safety_reactivate_all)
    menu.addAction(self._act_reactivate)

  @staticmethod
  def _bind_menu_toggle(
    menu: QtWidgets.QMenu,
    label: str,
    checkbox: QtWidgets.QCheckBox,
    *,
    checked: bool | None = None,
    tooltip: str = "",
    slot=None,
  ) -> QtWidgets.QAction:
    if checked is not None:
      checkbox.setChecked(checked)
    act = menu.addAction(label)
    act.setCheckable(True)
    act.setChecked(checkbox.isChecked())
    if tooltip:
      act.setToolTip(tooltip)
      checkbox.setToolTip(tooltip)

    def _from_act(on: bool) -> None:
      if checkbox.isChecked() != on:
        checkbox.setChecked(on)

    def _from_chk(on: bool) -> None:
      if act.isChecked() != on:
        act.setChecked(on)

    act.toggled.connect(_from_act)
    checkbox.toggled.connect(_from_chk)
    if slot is not None:
      checkbox.toggled.connect(slot)
    return act

  def _build_filter_menu(self) -> None:
    menu = self.menuBar().addMenu("Szűrők")
    self._bind_menu_toggle(
      menu,
      "Térkép",
      self.chk_map,
      checked=True,
      slot=self._toggle_map,
    )
    self._bind_menu_toggle(
      menu,
      "QTH Example City",
      self.chk_home,
      checked=True,
      tooltip="Saját állomás a térképen",
      slot=self._apply_geo_display,
    )
    self._bind_menu_toggle(
      menu,
      "Távolság km",
      self.chk_km,
      checked=True,
      tooltip="Távolság QTH-tól (térkép + hely oszlop)",
      slot=self._apply_geo_display,
    )
    self._bind_menu_toggle(
      menu,
      "Propagation réteg",
      self.chk_prop,
      checked=True,
      tooltip="Áttetsző kék iránysugarak a térképen — utóbbi dekódok iránya",
      slot=self._on_prop_overlay_toggled,
    )
    self._bind_menu_toggle(
      menu,
      "Csak CQ",
      self.chk_cq_only,
      tooltip="Táblázat és térkép: csak CQ üzenetek (a napló mindent ment)",
      slot=self._on_cq_only_toggled,
    )

  def _build_pro_menu(self) -> None:
    menu = self.menuBar().addMenu("Pro adatok")
    self._act_pro = self._bind_menu_toggle(
      menu,
      "Pro adatok megjelenítése",
      self.chk_pro,
      tooltip="DSP oszlopok, irány/km, órás összesítő — AI elemzéshez",
      slot=self._apply_pro_ui,
    )
    menu.addSeparator()
    self._act_pro_dsp = self._bind_menu_toggle(
      menu,
      "DSP oszlopok",
      self.chk_pro_dsp,
      slot=self._apply_pro_columns,
    )
    self._act_pro_geo = self._bind_menu_toggle(
      menu,
      "Irány / km",
      self.chk_pro_geo,
      slot=self._apply_pro_columns,
    )
    self._act_pro_hourly = self._bind_menu_toggle(
      menu,
      "Órás összesítő",
      self.chk_pro_hourly,
      slot=self._apply_pro_ui,
    )
    for act in (self._act_pro_dsp, self._act_pro_geo, self._act_pro_hourly):
      act.setEnabled(False)

  def _build_audio_menu(self) -> None:
    menu = self.menuBar().addMenu("Hangkártya")
    panel = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(panel)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(6)

    src_row = QtWidgets.QHBoxLayout()
    src_row.addWidget(QtWidgets.QLabel("Bemenet:"))
    self.combo_src.setMinimumWidth(280)
    src_row.addWidget(self.combo_src, stretch=1)
    lay.addLayout(src_row)
    lay.addWidget(self.btn_linein)
    meters_row = QtWidgets.QHBoxLayout()
    meters_row.addWidget(self.meter_raw)
    meters_row.addWidget(self.meter_out)
    lay.addLayout(meters_row)
    lay.addWidget(self.chk_auto_gain)
    lay.addWidget(self._gain_auto_wrap)
    lay.addWidget(self._gain_manual_wrap)

    action = QtWidgets.QWidgetAction(self)
    action.setDefaultWidget(panel)
    menu.addAction(action)

  def _build_error_log_menu(self) -> None:
    menu = self.menuBar().addMenu("Hibanapló")
    self._act_error_log_open = menu.addAction("Megnyitás…")
    self._act_error_log_open.setToolTip("Utolsó hibák és teendők — max. 100 bejegyzés")
    self._act_error_log_open.triggered.connect(self._open_error_log)
    menu.addSeparator()
    self._act_error_log_status = QtWidgets.QAction("", self)
    self._act_error_log_status.setEnabled(False)
    menu.addAction(self._act_error_log_status)
    self._update_error_log_menu()

  def _update_error_log_menu(self) -> None:
    n = self._error_journal.count
    self._act_error_log_status.setText(f"{n} / {MAX_ENTRIES} bejegyzés")

  def _open_error_log(self) -> None:
    ErrorLogDialog.open_journal(self._error_journal, self)
    self._update_error_log_menu()

  def _log_gui_error(self, code: str, detail: str = "", *, dedup: bool = True) -> None:
    if report_error(code, detail, dedup=dedup):
      self._update_error_log_menu()

  def _inject_error_test(self, code: str, detail: str = "") -> None:
    if code not in ALL_CODES:
      report_raw = f"Ismeretlen kód: {code}"
      self._log_gui_error("system_io_overload", report_raw, dedup=False)
      return
    self._log_gui_error(code, detail or "injektálás (teszt)", dedup=False)

  def _inject_all_errors_test(self) -> None:
    self._error_journal.clear()
    for code in ALL_CODES:
      self._inject_error_test(code, f"teszt: {code}")
    self._write_gui_live_status(note="ERROR_INJECT_ALL", force=True)

  def _sync_gain_panel(self, *_args) -> None:
    auto = self.chk_auto_gain.isChecked()
    self._gain_auto_wrap.setVisible(auto)
    self._gain_manual_wrap.setVisible(not auto)
    self.slider_gain.setEnabled(not auto)
    self.spin_target.setEnabled(auto)

  def _update_safety_menu(self) -> None:
    self._act_safety_status.setText(status_summary(self._safety_snap))
    tripped = self._safety_snap.tripped
    self._act_reactivate.setEnabled(tripped or not self._safety_snap.mcu_active)
    has_esp = bool(self._station.ptt_port)
    self._act_esp_unlock.setEnabled(has_esp)
    armed = self.btn_ptt.isEnabled() and self.btn_ptt.isChecked()
    self._act_mcu_shutdown.setEnabled(not tripped and (armed or self._safety_snap.mcu_active))

  def _show_safety_trip_banner(self) -> None:
    QtWidgets.QMessageBox.warning(
      self,
      "Biztonsági tiltás",
      f"A program biztonsági tiltással indult.\n\n{self._safety_snap.reason}\n\n"
      "Biztonság menü → ESP32 feloldás vagy Összes újraaktiválás a folytatáshoz.",
    )

  def _on_ptt_emergency(self, detail: str) -> None:
    QtCore.QMetaObject.invokeMethod(
      self,
      "_safety_trip_ui",
      QtCore.Qt.QueuedConnection,
      QtCore.Q_ARG(str, detail),
    )

  @QtCore.pyqtSlot(str)
  def _safety_trip_ui(self, reason: str) -> None:
    self._safety_trip(f"Ragadó PTT: {reason}")

  def _safety_trip(self, reason: str) -> None:
    self._ptt_watchdog.stop()
    self._operator.halt_transmission("")
    if hasattr(self._ptt, "shutdown"):
      self._ptt.shutdown()
    else:
      self._ptt.ptt_off()
    self._line_guard.release()
    self._operator.set_armed(False)
    self._operator.abort_qso("biztonság")
    self.btn_ptt.setChecked(False)
    self.btn_ptt.setEnabled(False)
    mark_tripped(self._safety_snap, reason)
    save_safety_state(self._safety_snap)
    self._act_watchdog.setChecked(False)
    self._act_line_guard.setChecked(False)
    self._update_safety_menu()
    self._write_gui_live_status(note=f"SAFETY_TRIP:{reason}")
    self._log_gui_error("safety_trip_stuck_ptt", reason, dedup=False)
    QtWidgets.QMessageBox.critical(
      self,
      "Biztonsági leállítás",
      f"{reason}\n\n"
      "PTT, vonalkimenet és ESP32 letiltva.\n"
      "Ellenőrizd a rádiót, majd: Biztonság → ESP32 feloldás vagy Összes újraaktiválás.",
    )

  def _safety_shutdown_mcu(self) -> None:
    if hasattr(self._ptt, "shutdown"):
      self._ptt.shutdown()
    else:
      self._ptt.ptt_off()
    self._operator.set_armed(False)
    self.btn_ptt.setChecked(False)
    self.btn_ptt.setEnabled(False)
    self._safety_snap.mcu_active = False
    save_safety_state(self._safety_snap)
    self._update_safety_menu()
    self.lbl_status.setText("ESP32 leállítva (SHUTDOWN) — újraindítás: Biztonság menü")
    self._log_gui_error("esp_shutdown", "SAFETY_LOCK bekapcsolt", dedup=False)

  def _resume_esp_after_lock(self, reason: str, *, trip_on_fail: bool = True) -> bool:
    """ESP SAFETY_LOCK feloldás — reboot / ragadó PTT után."""
    if not hasattr(self._ptt, "resume"):
      return False
    if not self._ptt.resume():
      err = getattr(self._ptt, "last_error", "ESP32 RESUME sikertelen")
      if trip_on_fail:
        mark_tripped(self._safety_snap, f"ESP LOCK ({reason}): {err}")
        save_safety_state(self._safety_snap)
        self.btn_ptt.setEnabled(False)
      self._last_tx_error = err
      self.lbl_status.setText(f"TX tiltva — ESP LOCK: {err}")
      self._write_gui_live_status(note=f"ESP_LOCK:{err}")
      self._log_gui_error("esp_resume_fail", f"{reason}: {err}")
      return False
    self._ptt.sync_time()
    self._ptt_ok = self._ptt.ping()
    self._last_tx_error = ""
    self._safety_snap.mcu_active = True
    save_safety_state(self._safety_snap)
    self.lbl_status.setText(f"ESP LOCK feloldva ({reason})")
    self._write_gui_live_status(note=f"ESP_RESUMED:{reason}")
    return True

  def _safety_unlock_esp(self) -> None:
    """Kézi ESP32 RESUME — SAFETY_LOCK feloldás a Biztonság menüből."""
    inner = getattr(self._ptt, "_inner", self._ptt)
    if not self._station.ptt_port:
      QtWidgets.QMessageBox.information(
        self,
        "ESP32 feloldás",
        "Nincs ESP32 soros port beállítva.",
      )
      return
    lock_txt = "?"
    if isinstance(inner, Esp32Ptt):
      lock_txt = "LOCK=1 (tiltva)" if inner.status().get("lock") else "LOCK=0 (szabad)"
    if self._resume_esp_after_lock("kézi menü", trip_on_fail=False):
      reason = (self._safety_snap.reason or "").upper()
      if self._safety_snap.tripped and ("ESP" in reason or "LOCK" in reason):
        mark_reactivated(
          self._safety_snap,
          watchdog=self._act_watchdog.isChecked(),
          line_guard=self._act_line_guard.isChecked(),
          mcu=True,
        )
        save_safety_state(self._safety_snap)
        self.btn_ptt.setEnabled(True)
      self._update_safety_menu()
      QtWidgets.QMessageBox.information(
        self,
        "ESP32 feloldás",
        f"RESUME sikeres.\nElőtte: {lock_txt}\n\n"
        "Ha a PTT gomb még tiltva van, használd: Összes újraaktiválás.",
      )
      return
    err = getattr(self._ptt, "last_error", "ismeretlen hiba")
    self._log_gui_error("esp_resume_fail", f"Előtte: {lock_txt}. {err}", dedup=False)
    QtWidgets.QMessageBox.warning(
      self,
      "ESP32 feloldás sikertelen",
      f"Az ESP32 nem fogadta a RESUME parancsot.\nElőtte: {lock_txt}\n\n{err}",
    )

  def _safety_reactivate_silent(self) -> bool:
    """Újraaktiválás párbeszéd nélkül — operator_in / automata helyreállítás."""
    if hasattr(self._ptt, "resume"):
      if not self._ptt.resume():
        return False
    self._ptt.sync_time()
    self._ptt_ok = self._ptt.ping()
    if self._act_watchdog.isChecked() and self._station.ptt_port:
      self._ptt_watchdog.set_enabled(True)
      self._ptt_watchdog.reset()
    if self._act_line_guard.isChecked():
      self._line_guard.set_enabled(True)
      self._line_guard.acquire()
    mark_reactivated(
      self._safety_snap,
      watchdog=self._act_watchdog.isChecked(),
      line_guard=self._act_line_guard.isChecked(),
      mcu=True,
    )
    save_safety_state(self._safety_snap)
    self.btn_ptt.setEnabled(True)
    self._last_tx_error = ""
    self._update_safety_menu()
    self.lbl_status.setText("Biztonsági rendszer újraaktiválva")
    self._write_gui_live_status(note="SAFETY_REACTIVATED")
    return True

  def _safety_reactivate_all(self) -> None:
    if (
      QtWidgets.QMessageBox.question(
        self,
        "Újraaktiválás",
        "ESP32 RESUME, vonalkimenet zárolás és PTT watchdog újraindítása.\n"
        "Biztosan folytatod?",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No,
      )
      != QtWidgets.QMessageBox.Yes
    ):
      return
    if not self._safety_reactivate_silent():
      err = getattr(self._ptt, "last_error", "ESP32 nem válaszol")
      self._log_gui_error("safety_reactivate_fail", err, dedup=False)
      QtWidgets.QMessageBox.warning(self, "ESP32 hiba", f"Újraindítás sikertelen:\n{err}")
      return
    self.lbl_status.setText(
      "Biztonsági rendszer újraaktiválva — kapcsold be a PTT-t ha adni szeretnél"
    )

  def _toggle_watchdog(self, on: bool) -> None:
    self._ptt_watchdog.set_enabled(on)
    self._safety_snap.watchdog_on = on
    save_safety_state(self._safety_snap)
    if on and not self._safety_snap.tripped:
      self._ptt_watchdog.reset()
    else:
      self._ptt_watchdog.stop()
    self._update_safety_menu()

  def _toggle_line_guard(self, on: bool) -> None:
    self._line_guard.set_enabled(on)
    self._safety_snap.line_guard_on = on
    save_safety_state(self._safety_snap)
    if on:
      self._line_guard.acquire()
    else:
      self._line_guard.release()
    self._update_safety_menu()

  def _poll_live_bridge(self) -> None:
    self._check_rx_stall()
    try:
      st = OPERATOR_IN.stat()
      if st.st_size == 0:
        self._operator_in_mtime = st.st_mtime
        return
      if st.st_mtime == self._operator_in_mtime:
        return
      self._operator_in_mtime = st.st_mtime
      text = OPERATOR_IN.read_text(encoding="utf-8").strip()
    except OSError:
      return
    if not text:
      return
    OPERATOR_IN.write_text("", encoding="utf-8")
    self._operator_in_mtime = -1.0
    for line in text.splitlines():
      cmd = line.strip().upper()
      if cmd.startswith("BAND "):
        parts = cmd.split()
        if len(parts) >= 2:
          band = parts[1].lower()
          if band.isdigit():
            band = f"{band}m"
          idx = self.combo_band.findText(band)
          if idx >= 0:
            self.combo_band.setCurrentIndex(idx)
            self._band_changed(band)
      elif cmd.startswith("DIAL "):
        parts = cmd.split()
        if len(parts) >= 2:
          self.spin_dial.setValue(float(parts[1]))
      elif cmd == "PTT_ON" and not self.btn_ptt.isChecked():
        self.btn_ptt.setChecked(True)
      elif cmd == "PTT_OFF" and self.btn_ptt.isChecked():
        self.btn_ptt.setChecked(False)
      elif cmd == "PRO_ON" and not self.chk_pro_tx.isChecked():
        self.chk_pro_tx.setChecked(True)
      elif cmd == "PRO_OFF" and self.chk_pro_tx.isChecked():
        self.chk_pro_tx.setChecked(False)
      elif cmd == "CQ_MODE_ON" and not self.chk_cq_uzem.isChecked():
        self.chk_cq_uzem.setChecked(True)
      elif cmd == "CQ_MODE_OFF" and self.chk_cq_uzem.isChecked():
        self.chk_cq_uzem.setChecked(False)
      elif cmd.startswith("PRO_PRIORITY "):
        raw = cmd.split(maxsplit=1)[1].lower() if " " in cmd else ""
        aliases = {
          "balanced": PriorityMode.BALANCED,
          "kiegyensúlyozott": PriorityMode.BALANCED,
          "distance": PriorityMode.DISTANCE,
          "távolság": PriorityMode.DISTANCE,
          "weak_dx": PriorityMode.WEAK_DX,
          "gyenge": PriorityMode.WEAK_DX,
          "strong_fast": PriorityMode.STRONG_FAST,
          "gyors": PriorityMode.STRONG_FAST,
        }
        mode = aliases.get(raw)
        if mode is None:
          try:
            mode = PriorityMode(raw)
          except ValueError:
            mode = PriorityMode.BALANCED
        self._station.pro.priority = mode
        self._sync_pro_priority_combo()
        self._operator.set_pro_config(self._station.pro)
      elif cmd == "MAP_OFF" and self.chk_map.isChecked():
        self.chk_map.setChecked(False)
      elif cmd == "MAP_ON" and not self.chk_map.isChecked():
        self.chk_map.setChecked(True)
      elif cmd.startswith("CQ_WAIT "):
        parts = cmd.split()
        if len(parts) >= 2:
          try:
            n = int(parts[1])
            idx = self._cq_wait_slider_index(n)
            self.slider_cq_wait.setValue(idx)
          except ValueError:
            pass
      elif cmd.startswith("PTT_PULSE"):
        parts = cmd.split()
        secs = float(parts[1]) if len(parts) > 1 else 2.0
        self._run_ptt_pulse(secs)
      elif cmd == "START_RX":
        if self.btn_start.isEnabled():
          self._start()
      elif cmd == "TX_TEST":
        self._run_tx_test()
      elif cmd == "ABORT_QSO":
        self._operator.abort_qso("operátor")
      elif cmd in ("SAFETY_RESUME", "RESUME_ESP"):
        self._safety_reactivate_silent()
      elif cmd == "SAFETY_UNLOCK":
        self._resume_esp_after_lock("operator_in")
      elif cmd == "ESP_SHUTDOWN":
        self._safety_shutdown_mcu()
        self._write_gui_live_status(note="ESP_SHUTDOWN", force=True)
      elif cmd == "ERROR_INJECT_ALL":
        self._inject_all_errors_test()
      elif cmd.startswith("ERROR_INJECT "):
        self._inject_error_test(cmd[13:].strip())
      elif cmd.startswith("CALL "):
        parts = cmd.split()
        if len(parts) >= 2:
          call = parts[1]
          hz = float(parts[2]) if len(parts) > 2 else 1867.0
          report = parts[3] if len(parts) > 3 else ""
          snr = int(parts[4]) if len(parts) > 4 else -15
          self._operator.engage_call(call, hz, rx_report=report, rx_snr=snr)
    self._write_gui_live_status(note=f"cmd:{text}")

  def _check_rx_stall(self) -> None:
    if self._engine is None or self._shutting_down:
      return
    now = time.monotonic()
    if self._decode_count > self._rx_stall_last_decode:
      self._rx_stall_last_decode = self._decode_count
      self._rx_stall_since_mono = now
      self._rx_stall_reported = False
      return
    stall_sec = 300.0
    elapsed = now - self._rx_stall_since_mono
    if elapsed >= stall_sec and not self._rx_stall_reported:
      report_error("rx_decode_stall", f"{int(elapsed)} s új dekód nélkül")
      self._rx_stall_reported = True
      self._update_error_log_menu()

  def _run_ptt_pulse(self, seconds: float) -> None:
    """Rövid PTT kulcsolás — GUI ESP-kapcsolat teszt (nem FT8 hang)."""
    def work() -> None:
      label = f"PTT teszt {seconds:.0f}s"
      self._on_tx_state(True, label)
      ok_on = self._ptt.ptt_on()
      time.sleep(max(0.5, seconds))
      ok_off = self._ptt.ptt_off()
      err = ""
      if not ok_on:
        err = getattr(self._ptt, "last_error", "ptt_on_failed")
        self._log_gui_error("esp_ptt_on_fail", err)
      elif not ok_off:
        err = getattr(self._ptt, "last_error", "ptt_off_failed")
        self._log_gui_error("esp_ptt_off_fail", err)
      self._on_tx_state(False, label, err)
      note = f"{label} OK" if not err else f"{label} HIBA: {err}"
      QtCore.QMetaObject.invokeMethod(
        self.lbl_status,
        "setText",
        QtCore.Qt.QueuedConnection,
        QtCore.Q_ARG(str, note),
      )

    threading.Thread(target=work, daemon=True, name="ptt-pulse").start()

  def _run_tx_test(self) -> None:
    """Egy FT8 CQ slot — PTT + hang, mint éles adáskor."""
    def work() -> None:
      msg = f"CQ {self._station.callsign} {self._station.grid4}"
      self._operator.tx.transmit(msg, 1500.0)

    threading.Thread(target=work, daemon=True, name="tx-test").start()

  def _write_gui_live_status(self, *, last_message: str = "", note: str = "", force: bool = False) -> None:
    phase = self._operator.phase.value
    partner = self._operator._active.remote_call if self._operator._active else ""
    now_mono = time.monotonic()
    important = bool(note) or phase != self._last_gui_status_phase or partner != self._last_gui_status_partner
    if (
      not force
      and not important
      and now_mono - self._last_gui_status_mono < 0.25
    ):
      return

    inner_ptt = getattr(self._ptt, "_inner", self._ptt)
    esp_lock: bool | None = None
    if isinstance(inner_ptt, Esp32Ptt):
      esp_lock = bool(inner_ptt.status().get("lock"))

    st = {
      "time_utc": time_iso_utc(time.time()),
      "callsign": self._station.callsign,
      "operator": self._station.operator_name,
      "band": self.combo_band.currentText(),
      "dial_mhz": self.spin_dial.value(),
      "rx_running": self._engine is not None,
      "ptt_armed": self.btn_ptt.isChecked(),
      "pro_operator": self.chk_pro_tx.isChecked(),
      "cq_only_mode": self.chk_cq_uzem.isChecked(),
      "cq_wait_periods": CQ_WAIT_PERIOD_CHOICES[self.slider_cq_wait.value()],
      "map_visible": self.chk_map.isChecked(),
      "pro_priority": self._station.pro.priority.value,
      "qso_phase": phase,
      "qso_partner": partner,
      "tx_active": self._tx_active,
      "last_tx_error": self._last_tx_error,
      "ptt_serial_ok": self._ptt_ok,
      "safety_tripped": self._safety_snap.tripped,
      "safety_reason": self._safety_snap.reason,
      "safety_watchdog": self._safety_snap.watchdog_on,
      "safety_line_guard": self._safety_snap.line_guard_on,
      "safety_mcu_active": self._safety_snap.mcu_active,
      "esp_lock": esp_lock,
      "decode_count": self._decode_count,
      "last_message": last_message,
      "note": note,
    }
    FORGALMI_LIVE.mkdir(parents=True, exist_ok=True)
    GUI_LIVE_STATUS.write_text(dumps_compact(st) + "\n", encoding="utf-8")
    self._last_gui_status_mono = now_mono
    self._last_gui_status_phase = phase
    self._last_gui_status_partner = partner

  def _apply_pro_ui(self, *_args) -> None:
    on = self.chk_pro.isChecked()
    for w, act in (
      (self.chk_pro_dsp, self._act_pro_dsp),
      (self.chk_pro_geo, self._act_pro_geo),
      (self.chk_pro_hourly, self._act_pro_hourly),
    ):
      w.setEnabled(on)
      act.setEnabled(on)
    if on and not any(
      (self.chk_pro_dsp.isChecked(), self.chk_pro_geo.isChecked(), self.chk_pro_hourly.isChecked())
    ):
      self.chk_pro_dsp.setChecked(True)
    self._apply_pro_columns()
    self.hour_wrap.setVisible(on and self.chk_pro_hourly.isChecked())
    if self.hour_wrap.isVisible():
      self._refresh_hourly_table()

  def _apply_pro_columns(self, *_args) -> None:
    show = self.chk_pro.isChecked()
    for col in range(self._PRO_COL, self._N_COLS):
      self.table.setColumnHidden(col, not show or not (
        (col in (7, 8, 9) and self.chk_pro_dsp.isChecked())
        or (col in (10, 11) and self.chk_pro_geo.isChecked())
      ))
    self.hour_wrap.setVisible(show and self.chk_pro_hourly.isChecked())

  def _refresh_hourly_table(self) -> None:
    if not self.hour_wrap.isVisible():
      return
    rows = self._session.hour_rows_for_ui()
    self.hour_table.setRowCount(len(rows))
    for r, row in enumerate(rows):
      compass = row.get("compass_bins") or {}
      compass_txt = " ".join(f"{k}:{v}" for k, v in sorted(compass.items())) or "—"
      snr_m = row.get("snr_mean")
      vals = [
        row.get("hour_utc", ""),
        str(row.get("decode_count", 0)),
        str(row.get("unique_call_count", 0)),
        str(row.get("new_stations", 0)),
        f"{snr_m:.1f}" if snr_m is not None else "—",
        str(row.get("mapped_decodes", 0)),
        compass_txt,
        str(row.get("clip_events", 0)),
      ]
      for c, txt in enumerate(vals):
        self.hour_table.setItem(r, c, QtWidgets.QTableWidgetItem(txt))

  def _sync_pro_priority_combo(self) -> None:
    idx_map = {
      PriorityMode.BALANCED: 0,
      PriorityMode.DISTANCE: 1,
      PriorityMode.WEAK_DX: 2,
      PriorityMode.STRONG_FAST: 3,
    }
    self.combo_pro_priority.setCurrentIndex(idx_map.get(self._station.pro.priority, 0))

  def _on_pro_tx_toggled(self, on: bool) -> None:
    self.combo_pro_priority.setEnabled(on)
    self._station.save_pro_enabled(on)
    self._station.pro.enabled = on
    self._operator.set_pro_config(self._station.pro)
    if on:
      self.chk_pro.setChecked(True)

  @staticmethod
  def _cq_wait_slider_index(periods: int) -> int:
    try:
      return CQ_WAIT_PERIOD_CHOICES.index(periods)
    except ValueError:
      return CQ_WAIT_PERIOD_CHOICES.index(3)

  def _cq_wait_label(self) -> str:
    n = CQ_WAIT_PERIOD_CHOICES[self.slider_cq_wait.value()]
    return f"{n} periódus ({n * 15}s)"

  def _on_cq_wait_changed(self, idx: int) -> None:
    periods = CQ_WAIT_PERIOD_CHOICES[idx]
    self.lbl_cq_wait.setText(f"{periods} periódus ({periods * 15}s)")
    self._station.save_cq_wait_periods(periods)
    self._operator.set_cq_wait_periods(periods)

  def _on_cq_uzem_toggled(self, on: bool) -> None:
    self._operator.set_cq_only_mode(on)
    if on and not self.chk_pro_tx.isChecked():
      self.chk_pro_tx.setChecked(True)

  def _on_pro_priority_changed(self, idx: int) -> None:
    modes = [
      PriorityMode.BALANCED,
      PriorityMode.DISTANCE,
      PriorityMode.WEAK_DX,
      PriorityMode.STRONG_FAST,
    ]
    if 0 <= idx < len(modes):
      self._station.pro.priority = modes[idx]
      self._operator.set_pro_config(self._station.pro)

  def _on_power_safe_toggled(self, on: bool) -> None:
    self._session.set_power_safe(on)

  def _style_ptt_button(self, armed: bool) -> None:
    if armed:
      self.btn_ptt.setStyleSheet(
        "QPushButton { background-color: #238636; color: white; font-weight: bold; }"
      )
      self.btn_ptt.setText("PTT ON")
    else:
      self.btn_ptt.setStyleSheet("")
      self.btn_ptt.setText("PTT")

  def _on_ptt_toggled(self, on: bool) -> None:
    if self._safety_snap.tripped:
      self.btn_ptt.blockSignals(True)
      self.btn_ptt.setChecked(False)
      self.btn_ptt.blockSignals(False)
      return
    self._style_ptt_button(on)
    self._operator.set_armed(on)
    self._write_gui_live_status(note="PTT_ON" if on else "PTT_OFF")
    if on and self._station.callsign in ("", "N0CALL"):
      QtWidgets.QMessageBox.warning(
        self,
        "Hívójel hiányzik",
        f"Állítsd be a hívójelet: {STATION_FILE}",
      )

  def _on_operator_status(self, text: str) -> None:
    QtCore.QMetaObject.invokeMethod(
      self,
      "_apply_operator_status_ui",
      QtCore.Qt.QueuedConnection,
      QtCore.Q_ARG(str, text),
    )

  @QtCore.pyqtSlot(str)
  def _apply_operator_status_ui(self, text: str) -> None:
    self.lbl_status.setText(f"Operátor: {text}")
    if text.startswith("QSO LOG "):
      parts = text.split()
      if len(parts) >= 3:
        call = _call_key(parts[2])
        self._qso_completed_calls.add(call)
        self._qso_active_call = ""
        self._style_qso_banner_completed(call)
        self._qso_banner_timer.start(12_000)
    elif self._operator._active is not None:
      self._qso_active_call = self._operator._active.remote_call
      self._qso_banner_timer.stop()
      self._refresh_qso_banner()
    elif text.startswith(("Feladva:", "Feladás:")):
      self._qso_active_call = ""
      self._refresh_qso_banner()
    self._apply_table_qso_highlights()
    self._write_gui_live_status(note=text)

  def _on_operator_tx(self, message: str) -> None:
    QtCore.QMetaObject.invokeMethod(
      self.lbl_footer,
      "setText",
      QtCore.Qt.QueuedConnection,
      QtCore.Q_ARG(str, f"TX → {message}"),
    )
    if self._operator._active is not None:
      self._qso_active_call = self._operator._active.remote_call
      QtCore.QMetaObject.invokeMethod(
        self,
        "_refresh_qso_banner",
        QtCore.Qt.QueuedConnection,
      )

  @QtCore.pyqtSlot()
  def _refresh_qso_banner(self) -> None:
    op = self._operator
    if op.phase == QsoPhase.CALLING_CQ:
      self.lbl_qso.setText("CQ-zás…")
      self.lbl_qso.setStyleSheet(
        "QLabel { background-color: #1f3d5c; color: #79c0ff; font-weight: bold; "
        "font-size: 14px; padding: 6px; border-radius: 4px; }"
      )
      return
    if op._active is not None:
      a = op._active
      phase_txt = {
        QsoPhase.ACTIVE: "váltás",
        QsoPhase.CLOSING: "zárás",
      }.get(a.phase, op.phase.value)
      grid = f" · {a.remote_grid}" if a.remote_grid else ""
      self.lbl_qso.setText(f"▶ Aktív QSO: {a.remote_call}{grid} — {phase_txt}")
      self.lbl_qso.setStyleSheet(
        "QLabel { background-color: #9e6a03; color: #ffffff; font-weight: bold; "
        "font-size: 14px; padding: 6px; border-radius: 4px; }"
      )
      self._qso_active_call = a.remote_call
      return
    self._style_qso_banner_idle()

  def _style_qso_banner_idle(self) -> None:
    self.lbl_qso.setText("QSO: nincs aktív kapcsolat")
    self.lbl_qso.setStyleSheet(
      "QLabel { background-color: #21262d; color: #8b949e; font-size: 13px; "
      "padding: 6px; border-radius: 4px; }"
    )

  def _style_qso_banner_completed(self, call: str) -> None:
    self.lbl_qso.setText(f"✓ Sikeres QSO: {call}")
    self.lbl_qso.setStyleSheet(
      "QLabel { background-color: #238636; color: #ffffff; font-weight: bold; "
      "font-size: 14px; padding: 6px; border-radius: 4px; }"
    )

  @staticmethod
  def _message_involves_call_fast(
    calls: tuple[str, ...], msg_up: str, call: str
  ) -> bool:
    cu = _call_key(call)
    for c in calls:
      if c == cu:
        return True
    return cu in msg_up

  def _reload_worked_qso_highlights(self) -> None:
    """Mai UTC QSO partnerek — restart / éjfél után zöld kiemelés."""
    day = today_log_day()
    if day != self._worked_utc_day:
      self._qso_completed_calls.clear()
      self._worked_utc_day = day
    self._qso_completed_calls.update(self._operator.naplo.worked_calls_today())
    self._apply_table_qso_highlights()

  def _maybe_roll_worked_utc_day(self) -> None:
    if today_log_day() != self._worked_utc_day:
      self._reload_worked_qso_highlights()

  def _open_log_search(self) -> None:
    dlg = LogSearchDialog(self)
    q = self.edit_log_search.text().strip()
    if q:
      dlg.edit_query.setText(q)
      dlg._run_search()
    dlg.exec_()

  def _style_tx_indicator(self, active: bool) -> None:
    if active:
      self.btn_tx_indicator.setStyleSheet(
        "QPushButton { background-color: #b62324; color: #ffffff; font-weight: bold; "
        "padding: 4px 14px; border-radius: 4px; border: 1px solid #f85149; }"
      )
    else:
      self.btn_tx_indicator.setStyleSheet(
        "QPushButton { background-color: #21262d; color: #8b949e; padding: 4px 14px; "
        "border-radius: 4px; border: 1px solid #30363d; }"
      )

  def _apply_table_qso_highlights(self) -> None:
    if not hasattr(self, "table"):
      return
    n = self.table.rowCount()
    if n == 0:
      return
    self._maybe_roll_worked_utc_day()
    active = self._qso_active_call
    cooldown = self._operator.outbound_cooldown_calls()
    default_brush = QtGui.QBrush()
    done_brush = QtGui.QBrush(self._BG_QSO_DONE)
    active_brush = QtGui.QBrush(self._BG_QSO_ACTIVE)
    cool_brush = QtGui.QBrush(self._BG_QSO_COOLDOWN)
    cool_fg = QtGui.QBrush(self._FG_QSO_COOLDOWN)
    ncol = self.table.columnCount()
    for row in range(n):
      msg_item = self.table.item(row, 5)
      if msg_item is None:
        continue
      msg = msg_item.text()
      calls = tuple(extract_callsigns_from_message(msg))
      msg_up = message_upper(msg)
      bg = None
      for done_call in self._qso_completed_calls:
        if self._message_involves_call_fast(calls, msg_up, done_call):
          bg = self._BG_QSO_DONE
          break
      if bg is None and active and self._message_involves_call_fast(calls, msg_up, active):
        bg = self._BG_QSO_ACTIVE
      if bg is None:
        for cd in cooldown:
          if self._message_involves_call_fast(calls, msg_up, cd):
            bg = self._BG_QSO_COOLDOWN
            break
      if bg == self._BG_QSO_DONE:
        brush, fg = done_brush, default_brush
      elif bg == self._BG_QSO_ACTIVE:
        brush, fg = active_brush, default_brush
      elif bg == self._BG_QSO_COOLDOWN:
        brush, fg = cool_brush, cool_fg
      else:
        brush, fg = default_brush, default_brush
      for col in range(ncol):
        item = self.table.item(row, col)
        if item is None:
          continue
        item.setBackground(brush if bg else default_brush)
        item.setForeground(fg if bg == self._BG_QSO_COOLDOWN else default_brush)

  def _on_tx_state(self, active: bool, message: str, error: str = "") -> None:
    self._tx_active = active
    if error:
      self._last_tx_error = error
      self._write_gui_live_status(note=f"TX HIBA: {error}", force=True)
      report_tx_error(message, error)
      self._update_error_log_menu()
      if "SAFETY_LOCK" in error.upper():
        QtCore.QMetaObject.invokeMethod(
          self,
          "_handle_safety_lock_tx_fail",
          QtCore.Qt.QueuedConnection,
          QtCore.Q_ARG(str, error),
        )
    if self._engine is not None:
      self._engine.set_rx_paused(active)
    note = f"PTT {'ON' if active else 'OFF'}: {message}"
    if error:
      note = f"TX HIBA: {error}"
    QtCore.QMetaObject.invokeMethod(
      self,
      "_apply_tx_state_ui",
      QtCore.Qt.QueuedConnection,
      QtCore.Q_ARG(bool, active),
      QtCore.Q_ARG(str, note),
    )

  @QtCore.pyqtSlot(str)
  def _handle_safety_lock_tx_fail(self, error: str) -> None:
    if self._resume_esp_after_lock("TX PTT_FAIL"):
      return
    if not self._safety_snap.tripped:
      self._safety_trip(f"ESP SAFETY_LOCK: {error}")

  @QtCore.pyqtSlot(bool, str)
  def _apply_tx_state_ui(self, active: bool, note: str) -> None:
    self.btn_tx_indicator.setVisible(active)
    self._style_tx_indicator(active)
    if active:
      msg = note.split(":", 1)[1].strip() if ":" in note else note
      self.lbl_status.setText(f"▶ {msg}" if msg else "▶ Adás folyamatban")
    elif note.startswith("TX HIBA"):
      self.lbl_status.setText(note)
    self._write_gui_live_status(note=note)

  def _home_for_geo(self) -> HomeQth | None:
    return DEFAULT_HOME if self.chk_home.isChecked() else None

  def _apply_geo_display(self, *_args) -> None:
    home = DEFAULT_HOME if self.chk_home.isChecked() else None
    grid_lookup.display.show_home_km = self.chk_km.isChecked()
    grid_lookup.display.show_city_km = False
    grid_lookup.display.home = home
    self.map_widget.configure(
      show_home=self.chk_home.isChecked(),
      show_km=self.chk_km.isChecked(),
      home=home,
      show_propagation=self.chk_prop.isChecked(),
    )
    self._refresh_location_column()

  def _refresh_location_column(self) -> None:
    for row in range(self.table.rowCount()):
      msg_item = self.table.item(row, 5)
      if msg_item is None:
        continue
      message = msg_item.text()
      try:
        text = grid_lookup.describe_message(message)
      except Exception as exc:
        text = f"hiba: {exc}"
      self.table.setItem(row, 6, QtWidgets.QTableWidgetItem(text))
      for call in extract_callsigns_from_message(message):
        self._session.set_location_for_call(call, text)

  def _on_prop_overlay_toggled(self, on: bool) -> None:
    self.map_widget.set_propagation_enabled(on)
    if on and self._engine is not None:
      self._prop_timer.start()
    elif not on:
      self._prop_timer.stop()

  def _tick_propagation_overlay(self) -> None:
    self.map_widget.tick_propagation(2.0)

  def _toggle_map(self, visible: bool) -> None:
    if visible and self._tx_active:
      self.chk_map.blockSignals(True)
      self.chk_map.setChecked(False)
      self.chk_map.blockSignals(False)
      if hasattr(self, "lbl_status"):
        self.lbl_status.setText("Térkép — várj az adás végére")
      return
    self.map_widget.setVisible(visible)
    QtCore.QTimer.singleShot(0, lambda v=visible: self._resize_map_splitter(v))

  def _resize_map_splitter(self, visible: bool) -> None:
    total = max(self.splitter.height(), 520)
    if visible:
      map_h = max(160, min(240, total // 3))
      self.map_widget.setMinimumHeight(120)
      self.splitter.setSizes([max(280, total - map_h), map_h])
      self._refresh_map_spots()
    else:
      self.map_widget.setMinimumHeight(0)
      self.splitter.setSizes([total, 0])

  def _update_footer(self, last_line: str = "") -> None:
    n_st = self._session.station_count()
    n_map = len(self._session.map_station_list(cq_only=self.chk_cq_only.isChecked()))
    shown = self.table.rowCount()
    if self.chk_cq_only.isChecked():
      base = (
        f"Dekódok: {shown} CQ látható / {self._decode_count} összes | "
        f"Hallott: {n_st} ({n_map} térképen, CQ)"
      )
    else:
      base = f"Dekódok: {self._decode_count} | Hallott állomások: {n_st} ({n_map} térképen)"
    self.lbl_footer.setText(f"{base}  |  {last_line}" if last_line else base)

  def _refresh_map_spots(self) -> None:
    if not self.chk_map.isChecked():
      return
    self.map_widget.set_spots(self._session.map_station_list(cq_only=self.chk_cq_only.isChecked()))

  def _note_propagation(self, message: str, snr: int) -> None:
    home = self._home_for_geo()
    mt, _, geo = message_preamble_geo(message, home)
    if self.chk_cq_only.isChecked() and mt != "cq":
      return
    self._note_propagation_geo(geo, snr)

  def _note_propagation_geo(self, geo: dict, snr: int) -> None:
    az = geo.get("azimuth_deg")
    if az is not None:
      self.map_widget.note_propagation(float(az), snr=snr)

  def _insert_table_row(
    self,
    row: int,
    *,
    decode_id: int,
    time_received: float,
    cycle: str,
    snr: int,
    dt: float,
    audio_hz: int,
    rf_khz: float,
    message: str,
    dsp: dict | None = None,
    msg_type: str | None = None,
    geo: dict | None = None,
    location_text: str = "…",
  ) -> None:
    self.table.insertRow(row)
    t = time_hms_utc(time_received)
    rf = f"{rf_khz:.3f}"
    items = [t, cycle, f"{snr:+d}", f"{dt:.1f}", f"{audio_hz} ({rf} kHz)", message]
    for col, text in enumerate(items):
      item = QtWidgets.QTableWidgetItem(text)
      if col == 2 and snr >= 0:
        item.setForeground(QtGui.QBrush(QtGui.QColor("#3fb950")))
      if col == 0:
        item.setData(QtCore.Qt.UserRole, decode_id)
      self.table.setItem(row, col, item)

    if geo is None or msg_type is None:
      home = self._home_for_geo()
      mt, _, g = message_preamble_geo(message, home)
      if geo is None:
        geo = g
      if msg_type is None:
        msg_type = mt
    dsp = dsp or {}
    geo_az = geo.get("azimuth_deg")
    geo_dist = geo.get("distance_km")
    pro_items = [
      msg_type,
      f"{dsp.get('sync_score', 0):.2f}",
      str(dsp.get("ncheck", "—")),
      str(geo_az if geo_az is not None else "—"),
      str(geo_dist if geo_dist is not None else "—"),
    ]
    for i, text in enumerate(pro_items):
      self.table.setItem(row, self._PRO_COL + i, QtWidgets.QTableWidgetItem(text))
    self.table.setItem(row, 6, QtWidgets.QTableWidgetItem(location_text))
    self._apply_table_qso_highlights()

  def _rebuild_decode_table(self) -> None:
    self.table.setRowCount(0)
    cq_only = self.chk_cq_only.isChecked()
    for rec in reversed(self._session.decodes):
      if cq_only and rec.get("msg_type") != "cq":
        continue
      self._insert_table_row(
        0,
        decode_id=int(rec["id"]),
        time_received=float(rec["time_received"]),
        cycle=str(rec.get("cycle", "")),
        snr=int(rec.get("snr", 0)),
        dt=float(rec.get("dt", 0.0)),
        audio_hz=int(rec.get("audio_hz", 0)),
        rf_khz=float(rec.get("rf_khz", 0.0)),
        message=str(rec.get("message", "")),
        dsp=rec.get("dsp"),
        msg_type=str(rec.get("msg_type", "")),
        geo=rec.get("geo"),
        location_text="…",
      )
      threading.Thread(
        target=self._geo_lookup_thread,
        args=(int(rec["id"]), str(rec.get("message", ""))),
        daemon=True,
      ).start()
    while self.table.rowCount() > 500:
      self.table.removeRow(self.table.rowCount() - 1)

  def _on_cq_only_toggled(self, _checked: bool) -> None:
    self._rebuild_decode_table()
    self._refresh_map_spots()
    self._update_footer()

  def _save_session(self) -> None:
    if not self._session.decodes and not self._session.stations:
      QtWidgets.QMessageBox.information(self, "Mentés", "Nincs mit menteni ebben a munkamenetben.")
      return
    stem = self._session.default_export_stem()
    default_json = str(LOG_DIR / f"{stem}.json")
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
      self,
      "Munkamenet mentése (JSON)",
      default_json,
      "JSON (*.json);;Minden fájl (*)",
    )
    if not path:
      return
    json_path = Path(path)
    adif_path = json_path.with_suffix(".adi")
    try:
      paths = self._session.export_bundle(json_path)
      self._session.export_adif(adif_path)
    except Exception as exc:
      QtWidgets.QMessageBox.warning(self, "Mentés hiba", str(exc))
      return
    extra = []
    if "decodes_jsonl" in paths:
      extra.append(f"JSONL dekódok:\n{paths['decodes_jsonl']}")
    if "candidates_jsonl" in paths:
      extra.append(f"JSONL kandidátok:\n{paths['candidates_jsonl']}")
    if "hours_json" in paths:
      extra.append(f"Órás:\n{paths['hours_json']}")
    if "decodes_parquet" in paths:
      extra.append(f"Parquet:\n{paths['decodes_parquet']}")
    QtWidgets.QMessageBox.information(
      self,
      "Mentve",
      f"JSON:\n{json_path}\n\nADIF:\n{adif_path}\n\n" + "\n\n".join(extra),
    )

  def _fill_sources(self) -> None:
    self.combo_src.clear()
    default_idx = 0
    for i, s in enumerate(list_pulse_sources()):
      if "ggmorse" in s.name.lower():
        continue
      if not s.is_mic and "alsa_input" not in s.name:
        continue
      label = f"LINE-IN: {s.name}"
      self.combo_src.addItem(label, s.name)
      if s.name == DEFAULT_LINEIN:
        default_idx = self.combo_src.count() - 1
    if self.combo_src.count() == 0:
      self.combo_src.addItem(f"LINE-IN: {DEFAULT_LINEIN}", DEFAULT_LINEIN)
    else:
      self.combo_src.setCurrentIndex(default_idx)

  def _pulse_name(self) -> str:
    name = self.combo_src.currentData()
    return str(name) if name else DEFAULT_LINEIN

  def _apply_linein(self) -> None:
    name = self._pulse_name()
    try:
      set_line_in_port(name)
      proc = subprocess.run(
        ["pactl", "set-source-mute", name, "0"],
        capture_output=True,
        text=True,
        timeout=2.0,
      )
      if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "pactl mute hiba").strip()[:160]
        report_error("rx_linein_port", f"{name}: {err}")
        self._update_error_log_menu()
    except (subprocess.TimeoutExpired, OSError) as exc:
      report_error("rx_linein_port", f"{name}: {exc}")
      self._update_error_log_menu()
    self.lbl_status.setText(f"Line-in port beállítva: {name}")

  def _band_changed(self, band: str) -> None:
    defaults = {"40m": 7.074, "20m": 14.074, "30m": 10.136, "80m": 3.573}
    if band in defaults:
      self.spin_dial.setValue(defaults[band])

  def _dial_changed(self, _v: float) -> None:
    if self._engine is not None:
      self._engine.set_dial_mhz(self.spin_dial.value())

  def _gain_changed(self, *_args) -> None:
    manual = self.slider_gain.value() / 100.0
    self.lbl_gain.setText(f"{manual:.2f}×")
    if self._engine is not None:
      self._engine.feed.gain_auto = self.chk_auto_gain.isChecked()
      self._engine.feed.gain_manual = manual
      self._engine.feed.target_rms = float(self.spin_target.value())

  def _start(self) -> None:
    self._stop(halt_reason="")
    self._apply_linein()
    pulse = self._pulse_name()
    self._engine = Ft8Engine(
      dial_mhz=self.spin_dial.value(),
      band=self.combo_band.currentText(),
      pulse_name=pulse,
      on_decode=lambda r: self.decode_signal.emit(r),
      on_levels=lambda *a: self.levels_signal.emit(*a),
      on_candidate=lambda c, cycle, ts, _snap: self._session.add_candidate(c, cycle, ts),
      on_cycle_search=lambda cycle, cst, n, busy, ts, snap: self._on_cycle_search(
        cycle, cst, n, busy, ts, snap
      ),
    )
    self._gain_changed()
    home = self._home_for_geo()
    self._session.set_power_safe(self.chk_power_safe.isChecked())
    self._session.reset(
      self.combo_band.currentText(),
      self.spin_dial.value(),
      pulse_device=pulse,
      audio_settings=self._engine.get_audio_settings(),
      home=home,
    )
    self._session.antenna_note = self.edit_antenna.text().strip()
    self.map_widget.set_spots([])
    self.map_widget.clear_propagation()
    try:
      self._engine.start()
    except Exception as exc:
      report_error("rx_start_fail", str(exc)[:200])
      self._update_error_log_menu()
      self._engine = None
      self.btn_start.setEnabled(True)
      self.btn_stop.setEnabled(False)
      self.lbl_status.setText(f"⚠ RX indítás sikertelen: {exc}")
      return
    self._rx_stall_last_decode = self._decode_count
    self._rx_stall_since_mono = time.monotonic()
    self._rx_stall_reported = False
    self._operator.set_band(self.combo_band.currentText(), self.spin_dial.value())
    self.btn_start.setEnabled(False)
    self.btn_stop.setEnabled(True)
    if self.btn_ptt.isChecked() and not self._safety_snap.tripped:
      self._operator.set_armed(True)
    if self.chk_prop.isChecked() and self.chk_map.isChecked():
      self._prop_timer.start()
    self.lbl_status.setText(
      f"Vétel: {pulse} @ {self.spin_dial.value():.3f} MHz USB — várakozás FT8 ciklusokra (15 s)…"
    )

  def _stop(self, *, halt_reason: str = "stop") -> None:
    self._prop_timer.stop()
    self._operator.halt_transmission(halt_reason)
    if self._tx_active:
      self._on_tx_state(False, "", "")
    if self._engine is not None:
      eng = self._engine
      eng._on_decode = None
      eng._on_levels = None
      eng._on_candidate = None
      eng._on_cycle_search = None
      eng.stop()
      self._engine = None
    self._session.shutdown()
    self.btn_start.setEnabled(True)
    self.btn_stop.setEnabled(False)

  def _spin_shutdown_events(self) -> None:
    app = QtWidgets.QApplication.instance()
    if app is not None:
      app.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)

  def _on_cycle_search(
    self, cycle: str, cycle_start_time: float, n_candidates: int, busy_max: float | None, ts: float, snap
  ) -> None:
    self._last_cycle_for_audio = cycle
    self._session.note_cycle_search(cycle, cycle_start_time, n_candidates, busy_max, ts)
    if snap.raw_rms > 0:
      self._session.note_audio_levels(snap.raw_rms, snap.clip_frac, cycle, ts)
    # Operátor UI szálon, késleltetve (QTimer csak GUI threaden megbízható).
    self.cycle_op_signal.emit(cycle)
    self.cycle_signal.emit()

  @QtCore.pyqtSlot(str)
  def _on_cycle_operator_delayed(self, cycle: str) -> None:
    QtCore.QTimer.singleShot(
      self._CYCLE_OP_DELAY_MS,
      lambda c=cycle: self._on_cycle_operator(c, time.time()),
    )

  @QtCore.pyqtSlot(str, float)
  def _on_cycle_operator(self, cycle: str, ts: float) -> None:
    """Operátor ciklus — UI szálon, dekódok után (ne retry RR73 után)."""
    self._operator.on_cycle(cycle, ts)

  def _on_decode_ui(self, report: DecodeReport) -> None:
    if self._shutting_down:
      return
    self._operator.on_decode(report)
    if self._operator._active is not None:
      self._qso_active_call = self._operator._active.remote_call
      self._refresh_qso_banner()
    self._decode_count += 1
    self._decode_seq += 1
    decode_id = self._decode_seq
    home = self._home_for_geo()
    msg_type, calls, geo = message_preamble_geo(report.message, home)
    self._decode_geo[decode_id] = geo
    self._decode_calls[decode_id] = calls
    if len(self._decode_geo) > 256:
      oldest = next(iter(self._decode_geo))
      self._decode_geo.pop(oldest, None)
      self._decode_calls.pop(oldest, None)

    self._session.add_decode(
      decode_id=decode_id,
      message=report.message,
      snr=report.snr,
      rf_khz=report.rf_khz,
      cycle=report.cycle,
      audio_hz=report.audio_hz,
      dt=report.dt,
      time_received=report.time_received,
      cycle_start_utc=report.cycle_start_utc,
      dsp=report.dsp,
      audio=report.audio,
      geo=geo,
      msg_type=msg_type,
      calls=calls,
    )

    if not self.chk_cq_only.isChecked() or msg_type == "cq":
      self._insert_table_row(
        0,
        decode_id=decode_id,
        time_received=report.time_received,
        cycle=report.cycle,
        snr=report.snr,
        dt=report.dt,
        audio_hz=report.audio_hz,
        rf_khz=report.rf_khz,
        message=report.message,
        dsp=report.dsp,
        msg_type=msg_type,
        geo=geo,
      )
      threading.Thread(
        target=self._geo_lookup_thread,
        args=(decode_id, report.message),
        daemon=True,
      ).start()
      if self.table.rowCount() > 500:
        self.table.removeRow(self.table.rowCount() - 1)

    if self.chk_map.isChecked():
      if not self.chk_cq_only.isChecked() or msg_type == "cq":
        self._note_propagation_geo(geo, report.snr)
      now_mono = time.monotonic()
      if now_mono - self._map_refresh_mono >= 2.0:
        self._map_refresh_mono = now_mono
        self._refresh_map_spots()

    now_mono = time.monotonic()
    if now_mono - self._footer_refresh_mono >= 1.0 or report.snr >= 0:
      self._footer_refresh_mono = now_mono
      self._update_footer(report.wsjtx_line)
    now_mono = time.monotonic()
    if now_mono - self._highlight_refresh_mono >= 1.0:
      self._highlight_refresh_mono = now_mono
      self._apply_table_qso_highlights()
    now_mono = time.monotonic()
    if now_mono - self._hourly_refresh_mono >= 2.0:
      self._hourly_refresh_mono = now_mono
      self._refresh_hourly_table()
    self._write_gui_live_status(last_message=report.message)

  def _geo_lookup_thread(self, decode_id: int, message: str) -> None:
    text = "—"
    try:
      text = grid_lookup.describe_message(message)
    except Exception as exc:
      text = f"hiba: {exc}"
    finally:
      self.geo_signal.emit(decode_id, text)

  def _on_geo_ui(self, decode_id: int, text: str) -> None:
    message = ""
    for row in range(self.table.rowCount()):
      t0 = self.table.item(row, 0)
      if t0 is None:
        continue
      if t0.data(QtCore.Qt.UserRole) == decode_id:
        self.table.setItem(row, 6, QtWidgets.QTableWidgetItem(text))
        msg_item = self.table.item(row, 5)
        message = msg_item.text() if msg_item else ""
        break
    if message and text != "—":
      geo = self._decode_geo.pop(decode_id, None)
      calls = self._decode_calls.pop(decode_id, None)
      if geo is None or calls is None:
        home = self._home_for_geo()
        _, call_list, g = message_preamble_geo(message, home)
        if geo is None:
          geo = g
        if calls is None:
          calls = call_list
      for call in calls:
        self._session.set_location_for_call(call, text)
      if self.chk_pro.isChecked() and self.chk_pro_geo.isChecked():
        for row in range(self.table.rowCount()):
          t0 = self.table.item(row, 0)
          if t0 and t0.data(QtCore.Qt.UserRole) == decode_id:
            geo_az = geo.get("azimuth_deg")
            geo_dist = geo.get("distance_km")
            if geo_az is not None:
              self.table.setItem(row, 10, QtWidgets.QTableWidgetItem(str(geo_az)))
            if geo_dist is not None:
              self.table.setItem(row, 11, QtWidgets.QTableWidgetItem(str(geo_dist)))
            break

  def _on_levels_ui(
    self, raw_rms: float, out_rms: float, peak: float, clip: float, gain: float
  ) -> None:
    self.meter_raw.set_values(raw_rms, peak, 0.0)
    self.meter_out.set_values(out_rms, peak, clip)
    if self._last_cycle_for_audio:
      self._session.note_audio_levels(raw_rms, clip, self._last_cycle_for_audio, time.time())
    if clip > 0.05 and self.chk_auto_gain.isChecked():
      self.lbl_status.setText(
        f"⚠ Clipping ({100*clip:.0f}%) — auto erősítés csökkenti (effektív {gain:.2f}×)"
      )

  def closeEvent(self, event) -> None:
    if self._shutting_down:
      event.accept()
      return
    self._shutting_down = True
    self._live_timer.stop()
    self._prop_timer.stop()
    self._qso_banner_timer.stop()
    self._operator.tx.on_state = None
    self._stop(halt_reason="close")
    self._operator.shutdown(spin=self._spin_shutdown_events)
    self._ptt_watchdog.stop()
    if hasattr(self._ptt, "close"):
      self._ptt.close()
    self._line_guard.release()
    try:
      import matplotlib.pyplot as plt

      plt.close(self.map_widget.fig)
    except Exception:
      pass
    event.accept()
    super().closeEvent(event)
    app = QtWidgets.QApplication.instance()
    if app is not None:
      app.quit()


def main() -> None:
  import sys

  app = QtWidgets.QApplication(sys.argv)
  app.setStyle("Fusion")
  w = Ft8Window()
  w.show()
  sys.exit(app.exec_())


if __name__ == "__main__":
  main()
