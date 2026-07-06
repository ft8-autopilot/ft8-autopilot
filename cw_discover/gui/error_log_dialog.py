"""Hibanapló megjelenítő párbeszédablak."""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

from cw_discover.gui.error_journal import ErrorJournal, MAX_ENTRIES


class ErrorLogDialog(QtWidgets.QDialog):
  def __init__(self, journal: ErrorJournal, parent=None) -> None:
    super().__init__(parent)
    self._journal = journal
    self.setWindowTitle("Hibanapló")
    self.resize(760, 480)

    layout = QtWidgets.QVBoxLayout(self)
    self.lbl_info = QtWidgets.QLabel()
    self.lbl_info.setWordWrap(True)
    layout.addWidget(self.lbl_info)

    self.text = QtWidgets.QPlainTextEdit()
    self.text.setReadOnly(True)
    self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    font = QtGui.QFont("Monospace")
    font.setStyleHint(QtGui.QFont.Monospace)
    self.text.setFont(font)
    layout.addWidget(self.text, stretch=1)

    row = QtWidgets.QHBoxLayout()
    btn_refresh = QtWidgets.QPushButton("Frissítés")
    btn_refresh.clicked.connect(self.refresh)
    row.addWidget(btn_refresh)
    btn_clear = QtWidgets.QPushButton("Törlés")
    btn_clear.setToolTip("Az összes bejegyzés törlése")
    btn_clear.clicked.connect(self._clear)
    row.addWidget(btn_clear)
    row.addStretch(1)
    btn_close = QtWidgets.QPushButton("Bezárás")
    btn_close.clicked.connect(self.accept)
    row.addWidget(btn_close)
    layout.addLayout(row)

    self.refresh()

  def refresh(self) -> None:
    n = self._journal.count
    self.lbl_info.setText(
      f"Utolsó {min(n, MAX_ENTRIES)} hiba (max. {MAX_ENTRIES} — a legrégebbi felülíródik). "
      "Legújabb felül."
    )
    blocks = [e.format_block() for e in self._journal.entries_newest_first()]
    self.text.setPlainText("\n\n".join(blocks) if blocks else "Nincs rögzített hiba. 🎉")

  def _clear(self) -> None:
    if (
      QtWidgets.QMessageBox.question(
        self,
        "Hibanapló törlése",
        "Biztosan törlöd az összes bejegyzést?",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No,
      )
      != QtWidgets.QMessageBox.Yes
    ):
      return
    self._journal.clear()
    self.refresh()

  @staticmethod
  def open_journal(journal: ErrorJournal, parent: QtWidgets.QWidget | None) -> None:
    dlg = ErrorLogDialog(journal, parent)
    dlg.exec_()
