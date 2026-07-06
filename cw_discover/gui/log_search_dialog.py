"""Mai log kereső párbeszédablak."""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from cw_discover.ft8.log_search import search_today_logs, source_label


class LogSearchDialog(QtWidgets.QDialog):
  def __init__(self, parent=None) -> None:
    super().__init__(parent)
    self.setWindowTitle("Keresés — mai naplók")
    self.resize(720, 420)
    layout = QtWidgets.QVBoxLayout(self)

    row = QtWidgets.QHBoxLayout()
    self.edit_query = QtWidgets.QLineEdit()
    self.edit_query.setPlaceholderText("Hívójel, üzenet… (? egy karakter, * tetszőleges)")
    self.edit_query.returnPressed.connect(self._run_search)
    row.addWidget(self.edit_query, stretch=1)
    btn = QtWidgets.QPushButton("Keresés")
    btn.clicked.connect(self._run_search)
    row.addWidget(btn)
    layout.addLayout(row)

    self.lbl_count = QtWidgets.QLabel("Írj be legalább egy karaktert.")
    layout.addWidget(self.lbl_count)

    self.table = QtWidgets.QTableWidget(0, 4)
    self.table.setHorizontalHeaderLabels(["Forrás", "Idő (CET)", "Találat", "Részlet"])
    self.table.horizontalHeader().setStretchLastSection(True)
    self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
    self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    self.table.setAlternatingRowColors(True)
    layout.addWidget(self.table)

    close_btn = QtWidgets.QPushButton("Bezárás")
    close_btn.clicked.connect(self.accept)
    layout.addWidget(close_btn)

  def _run_search(self) -> None:
    q = self.edit_query.text().strip()
    if not q:
      self.lbl_count.setText("Írj be legalább egy karaktert.")
      self.table.setRowCount(0)
      return
    hits = search_today_logs(q)
    self.table.setRowCount(len(hits))
    for row, hit in enumerate(hits):
      cells = (source_label(hit.source), hit.time_text, hit.summary, hit.detail)
      for col, text in enumerate(cells):
        self.table.setItem(row, col, QtWidgets.QTableWidgetItem(text))
    self.lbl_count.setText(f"{len(hits)} találat — mai nap (idők CET-ben)")

  @staticmethod
  def open_search(parent: QtWidgets.QWidget | None) -> None:
    dlg = LogSearchDialog(parent)
    dlg.exec_()
