"""Audio szintmérő sáv — nyers / dekóder bemenet."""
from __future__ import annotations

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets


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
