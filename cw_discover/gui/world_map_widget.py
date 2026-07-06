"""Világtérkép gombostűkkel — matplotlib + Natural Earth shapefile."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.collections import PatchCollection
from matplotlib.figure import Figure
from matplotlib.patches import Polygon
from PyQt5 import QtCore, QtWidgets

from cw_discover.ft8.home_qth import HomeQth
from cw_discover.ft8.grid_geo import _haversine_km
from cw_discover.ft8.propagation_overlay import PropagationOverlay, destination_point, snr_weight
from cw_discover.ft8.session_log import HeardStation

_SHAPE_CANDIDATES = [
  Path(__file__).resolve().parents[2] / "data" / "ne_50m_admin_0_countries.shp",
]

_DEFAULT_XLIM = (-180.0, 180.0)
_DEFAULT_YLIM = (-70.0, 85.0)
_ZOOM_FACTOR = 1.18
_PROP_WEDGE_KM = 2600.0
_PROP_COLOR = "#388bfd"

_land_cache: list[np.ndarray] | None = None


def _load_land_polygons() -> list[np.ndarray]:
  global _land_cache
  if _land_cache is not None:
    return _land_cache
  import shapefile

  shp_path = next((p for p in _SHAPE_CANDIDATES if p.exists()), None)
  if shp_path is None:
    _land_cache = []
    return _land_cache

  sf = shapefile.Reader(str(shp_path))
  polys: list[np.ndarray] = []
  for shape in sf.shapes():
    pts = np.asarray(shape.points, dtype=np.float64)
    if pts.size < 4:
      continue
    polys.append(pts)
  _land_cache = polys
  return polys


class SafeFigureCanvas(FigureCanvas):
  """Matplotlib crash elkerülése — nulla/negatív splitter méretnél."""

  def resizeEvent(self, event) -> None:
    sz = event.size()
    if sz.width() < 8 or sz.height() < 8:
      return
    try:
      super().resizeEvent(event)
    except ValueError:
      pass


class WorldMapWidget(QtWidgets.QWidget):
  def __init__(self, parent=None) -> None:
    super().__init__(parent)
    self._spots: list[HeardStation] = []
    self._show_home = True
    self._show_km = True
    self._show_propagation = True
    self._home: HomeQth | None = HomeQth.default()
    self._propagation = PropagationOverlay()
    self._view_xlim: tuple[float, float] | None = None
    self._view_ylim: tuple[float, float] | None = None
    self._pan_anchor: tuple[float, float, tuple[float, float], tuple[float, float]] | None = None
    self._static_ready = False
    self._dynamic_artists: list = []
    self.fig = Figure(figsize=(10, 3.2), facecolor="#0d1117")
    self.canvas = SafeFigureCanvas(self.fig)
    self.ax = self.fig.add_subplot(111)
    layout = QtWidgets.QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(self.canvas)
    self.lbl_zoom_hint = QtWidgets.QLabel(
      "Görgő: nagyítás · Bal gomb húzás: mozgatás · Dupla klikk: teljes nézet"
    )
    self.lbl_zoom_hint.setStyleSheet("color: #6e7681; font-size: 10px; padding: 2px 6px;")
    layout.addWidget(self.lbl_zoom_hint)
    self.canvas.mpl_connect("scroll_event", self._on_scroll)
    self.canvas.mpl_connect("button_press_event", self._on_press)
    self.canvas.mpl_connect("button_release_event", self._on_release)
    self.canvas.mpl_connect("motion_notify_event", self._on_motion)
    self._redraw_timer = QtCore.QTimer(self)
    self._redraw_timer.setSingleShot(True)
    self._redraw_timer.setInterval(200)
    self._redraw_timer.timeout.connect(self._redraw_spots_now)
    self._redraw_pending = False
    self._ensure_static_map()
    self._redraw_spots()

  def reset_view(self) -> None:
    self._view_xlim = None
    self._view_ylim = None
    self._apply_limits()
    self.canvas.draw_idle()

  def _current_limits(self) -> tuple[tuple[float, float], tuple[float, float]]:
    if self._view_xlim is not None and self._view_ylim is not None:
      return self._view_xlim, self._view_ylim
    return _DEFAULT_XLIM, _DEFAULT_YLIM

  @staticmethod
  def _fit_axis(
    lo: float,
    hi: float,
    *,
    min_span: float,
    bounds: tuple[float, float],
  ) -> tuple[float, float]:
    """Clamp pan/zoom window inside map bounds; never return zero-width limits."""
    b0, b1 = bounds
    lo, hi = sorted((lo, hi))
    if hi - lo < min_span:
      center = 0.5 * (lo + hi)
      lo = center - min_span / 2
      hi = center + min_span / 2
    width = hi - lo
    max_width = b1 - b0
    if width >= max_width:
      return b0, b1
    if lo < b0:
      hi += b0 - lo
      lo = b0
    if hi > b1:
      lo -= hi - b1
      hi = b1
    lo = max(b0, lo)
    hi = min(b1, hi)
    if hi - lo < min_span:
      center = max(b0 + min_span / 2, min(b1 - min_span / 2, 0.5 * (lo + hi)))
      lo = center - min_span / 2
      hi = center + min_span / 2
    return lo, hi

  def _set_limits(self, xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
    xmin, xmax = self._fit_axis(xlim[0], xlim[1], min_span=2.0, bounds=_DEFAULT_XLIM)
    ymin, ymax = self._fit_axis(ylim[0], ylim[1], min_span=1.5, bounds=_DEFAULT_YLIM)
    self._view_xlim = (xmin, xmax)
    self._view_ylim = (ymin, ymax)

  def _apply_limits(self) -> None:
    xlim, ylim = self._current_limits()
    if xlim[1] <= xlim[0]:
      xlim = _DEFAULT_XLIM
      self._view_xlim = xlim
    if ylim[1] <= ylim[0]:
      ylim = _DEFAULT_YLIM
      self._view_ylim = ylim
    self.ax.set_xlim(*xlim)
    self.ax.set_ylim(*ylim)

  def _refresh_canvas(self) -> None:
    """Zoom/pan: csak nézetváltás, kontinensek nem rajzolódnak újra."""
    self._apply_limits()
    self.canvas.draw_idle()

  def _on_scroll(self, event) -> None:
    if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
      return
    scale = 1.0 / _ZOOM_FACTOR if event.button == "up" else _ZOOM_FACTOR
    xlim, ylim = self._current_limits()
    new_w = (xlim[1] - xlim[0]) * scale
    new_h = (ylim[1] - ylim[0]) * scale
    relx = (event.xdata - xlim[0]) / max(xlim[1] - xlim[0], 1e-9)
    rely = (event.ydata - ylim[0]) / max(ylim[1] - ylim[0], 1e-9)
    nx0 = event.xdata - relx * new_w
    nx1 = nx0 + new_w
    ny0 = event.ydata - rely * new_h
    ny1 = ny0 + new_h
    self._set_limits((nx0, nx1), (ny0, ny1))
    self._refresh_canvas()

  def _on_press(self, event) -> None:
    if event.inaxes != self.ax:
      return
    if getattr(event, "dblclick", False):
      self.reset_view()
      return
    if event.button == 1 and event.xdata is not None and event.ydata is not None:
      self._pan_anchor = (event.xdata, event.ydata, self._current_limits()[0], self._current_limits()[1])
      self.canvas.setCursor(QtCore.Qt.ClosedHandCursor)

  def _on_release(self, _event) -> None:
    self._pan_anchor = None
    self.canvas.setCursor(QtCore.Qt.ArrowCursor)

  def _on_motion(self, event) -> None:
    if self._pan_anchor is None or event.inaxes != self.ax:
      return
    if event.xdata is None or event.ydata is None:
      return
    ax0, ay0, xlim, ylim = self._pan_anchor
    dx = event.xdata - ax0
    dy = event.ydata - ay0
    self._set_limits((xlim[0] - dx, xlim[1] - dx), (ylim[0] - dy, ylim[1] - dy))
    self._refresh_canvas()

  def configure(
    self,
    *,
    show_home: bool,
    show_km: bool,
    home: HomeQth | None,
    show_propagation: bool | None = None,
  ) -> None:
    self._show_home = show_home
    self._show_km = show_km
    self._home = home
    if show_propagation is not None:
      self._show_propagation = show_propagation
    self._schedule_spot_redraw()

  def set_propagation_enabled(self, enabled: bool) -> None:
    self._show_propagation = enabled
    if not enabled:
      self._propagation.reset()
    self._schedule_spot_redraw()

  def clear_propagation(self) -> None:
    self._propagation.reset()
    self._schedule_spot_redraw()

  def note_propagation(self, azimuth_deg: float | None, *, snr: int = 0) -> None:
    if not self._show_propagation or azimuth_deg is None:
      return
    self._propagation.note_azimuth(azimuth_deg, weight=snr_weight(snr))
    self._schedule_spot_redraw()

  def tick_propagation(self, dt_seconds: float) -> None:
    if not self._show_propagation:
      return
    self._propagation.tick(dt_seconds)
    if self._propagation.active():
      self._schedule_spot_redraw()

  def _ensure_static_map(self) -> None:
    """Kontinensek egyszer — zoom/pan nem hívja újra."""
    if self._static_ready:
      return
    ax = self.ax
    ax.clear()
    ax.set_facecolor("#161b22")
    polys = _load_land_polygons()
    if polys:
      patches = [Polygon(p, closed=True) for p in polys]
      land = PatchCollection(
        patches,
        facecolor="#21262d",
        edgecolor="#30363d",
        linewidths=0.25,
        zorder=1,
      )
      ax.add_collection(land)
    ax.set_aspect(1.45, adjustable="box")
    ax.grid(True, color="#30363d", linewidth=0.4, alpha=0.6)
    ax.tick_params(colors="#8b949e", labelsize=7)
    for spine in ax.spines.values():
      spine.set_color("#30363d")
    ax.set_xlabel("Hosszúság °K", color="#8b949e", fontsize=8)
    ax.set_ylabel("Szélesség °É", color="#8b949e", fontsize=8)
    self._static_ready = True
    self._apply_limits()

  def _track(self, artist) -> None:
    self._dynamic_artists.append(artist)

  def _clear_dynamic(self) -> None:
    for art in self._dynamic_artists:
      try:
        art.remove()
      except Exception:
        pass
    self._dynamic_artists.clear()
    self.ax.set_title("")

  def set_spots(self, spots: list[HeardStation]) -> None:
    self._spots = list(spots)
    self._schedule_spot_redraw()

  def add_spot(self, spot: HeardStation) -> None:
    self._spots.append(spot)
    self._schedule_spot_redraw()

  def _schedule_spot_redraw(self) -> None:
    if not self._redraw_pending:
      self._redraw_pending = True
      self._redraw_timer.start()

  def _redraw_spots_now(self) -> None:
    self._redraw_pending = False
    self._redraw_spots()

  def _wedge_polygon(
    self, lat0: float, lon0: float, az_center: float, half_deg: float, dist_km: float
  ) -> np.ndarray:
    lons = [lon0]
    lats = [lat0]
    steps = max(8, int(half_deg * 2))
    for t in np.linspace(-half_deg, half_deg, steps):
      lat, lon = destination_point(lat0, lon0, az_center + float(t), dist_km)
      lons.append(lon)
      lats.append(lat)
    lons.append(lon0)
    lats.append(lat0)
    return np.column_stack([lons, lats])

  def _draw_propagation_layer(self, ax) -> None:
    if not self._show_propagation or self._home is None:
      return
    if not self._propagation.active():
      return
    h = self._home
    patches: list[Polygon] = []
    alphas: list[float] = []
    for az, half, strength in self._propagation.wedge_specs():
      pts = self._wedge_polygon(h.lat, h.lon, az, half, _PROP_WEDGE_KM)
      patches.append(Polygon(pts, closed=True))
      alphas.append(0.05 + 0.28 * strength)
    if not patches:
      return
    coll = PatchCollection(
      patches,
      facecolor=_PROP_COLOR,
      edgecolor="none",
      alpha=alphas,
      zorder=2,
    )
    self._track(ax.add_collection(coll))

  def _redraw_spots(self) -> None:
    self._ensure_static_map()
    self._clear_dynamic()
    ax = self.ax

    self._draw_propagation_layer(ax)

    if self._show_home and self._home is not None:
      h = self._home
      self._track(
        ax.scatter(
          [h.lon],
          [h.lat],
          s=120,
          c="#d29922",
          marker="*",
          edgecolors="#f0f6fc",
          linewidths=0.8,
          zorder=6,
          alpha=1.0,
        )
      )
      self._track(
        ax.annotate(
          f"{h.name} ({h.grid})",
          (h.lon, h.lat),
          textcoords="offset points",
          xytext=(6, -10),
          fontsize=7,
          color="#d29922",
          fontweight="bold",
        )
      )

    if not self._spots:
      title = "Hallott állomások (0) — egyedi hívójel / állomás"
      if self._show_home and self._home:
        title += f" · QTH: {self._home.name}"
      if self._show_propagation and self._propagation.active():
        title += " · kék réteg = utóbbi propagation irány"
      ax.set_title(title, color="#8b949e", fontsize=9)
      self._apply_limits()
      self.canvas.draw_idle()
      return

    mapped = [s for s in self._spots if s.lat is not None and s.lon is not None]
    if mapped:
      snrs = [s.snr for s in mapped]
      colors = ["#3fb950" if s >= 0 else "#58a6ff" if s >= -10 else "#f0883e" for s in snrs]
      self._track(
        ax.scatter(
          [s.lon for s in mapped],
          [s.lat for s in mapped],
          s=42,
          c=colors,
          marker="o",
          edgecolors="#f0f6fc",
          linewidths=0.6,
          zorder=5,
          alpha=0.92,
        )
      )

    if self._show_km and self._show_home and self._home is not None:
      h = self._home
      for spot in mapped:
        (line,) = ax.plot(
          [h.lon, spot.lon],
          [h.lat, spot.lat],
          color="#484f58",
          linewidth=0.5,
          alpha=0.45,
          zorder=3,
        )
        self._track(line)

    # Felirat: a 40 legutóbb hallott térképes állomás (nem dict-beszúrási sorrend).
    label_spots = sorted(mapped, key=lambda s: s.last_heard, reverse=True)[:40]
    for spot in label_spots:
      label = spot.call or spot.grid
      if self._show_km and self._show_home and self._home is not None:
        d = _haversine_km(spot.lat, spot.lon, self._home.lat, self._home.lon)
        label = f"{label} {d:.0f}km"
      self._track(
        ax.annotate(
          label,
          (spot.lon, spot.lat),
          textcoords="offset points",
          xytext=(4, 4),
          fontsize=6,
          color="#c9d1d9",
          alpha=0.85,
        )
      )

    title = f"Hallott állomások ({len(self._spots)} hívójel a térképen)"
    if self._show_home and self._home:
      title += f" · QTH: {self._home.name}"
    if self._show_propagation and self._propagation.active():
      title += " · kék réteg = utóbbi propagation irány"
    ax.set_title(title, color="#c9d1d9", fontsize=9)
    self._apply_limits()
    self.canvas.draw_idle()
