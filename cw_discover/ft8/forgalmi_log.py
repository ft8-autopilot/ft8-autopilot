"""Forgalmi napló — magyar tab + ADIF 3.1 (LoTW / QRZ / hatóság)."""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cw_discover.ft8.atomic_io import AtomicJsonlSink
from cw_discover.ft8.grid_geo import _call_key, grid4_upper
from cw_discover.ft8.station_identity import FORGALMI_DIR, StationIdentity
from cw_discover.ft8.pro_operator import ProOperatorConfig

CET = ZoneInfo("Europe/Budapest")


@dataclass
class QsoRecord:
  call: str
  grid: str
  band: str
  dial_mhz: float
  mode: str = "FT8"
  rst_sent: str = ""
  rst_rcvd: str = ""
  time_on: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
  time_off: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
  tx_audio_hz: int = 0
  distance_km: float | None = None
  azimuth_deg: float | None = None
  comment: str = ""
  partner_name: str = ""
  partner_qth: str = ""
  grid_source: str = "message"

  @property
  def qso_id(self) -> str:
    return str(uuid.uuid4())

  def to_json(self, *, station: StationIdentity, qso_id: str) -> dict:
    return {
      "qso_id": qso_id,
      "time_on_iso": self.time_on.isoformat(),
      "time_off_iso": self.time_off.isoformat(),
      "call": self.call,
      "grid": self.grid,
      "grid_source": self.grid_source,
      "mode": self.mode,
      "band": self.band,
      "freq_mhz": self.dial_mhz,
      "freq_hz": int(self.dial_mhz * 1_000_000),
      "rst_sent": self.rst_sent,
      "rst_rcvd": self.rst_rcvd,
      "tx_audio_hz": self.tx_audio_hz,
      "distance_km": self.distance_km,
      "azimuth_deg": self.azimuth_deg,
      "comment": self.comment,
      "station_callsign": station.callsign,
      "operator": station.operator_name or station.callsign,
      "my_gridsquare": station.grid4,
      "tx_power_w": station.tx_power_w,
      "adif_blob": self.adif_fields(station),
    }

  def adif_fields(self, station: StationIdentity) -> dict[str, str]:
    freq = f"{self.dial_mhz:.6f}"
    return {
      "call": self.call,
      "gridsquare": grid4_upper(self.grid),
      "mode": self.mode,
      "band": self.band,
      "freq": freq,
      "rst_sent": self.rst_sent,
      "rst_rcvd": self.rst_rcvd,
      "qso_date": self.time_on.strftime("%Y%m%d"),
      "time_on": self.time_on.strftime("%H%M%S"),
      "qso_date_off": self.time_off.strftime("%Y%m%d"),
      "time_off": self.time_off.strftime("%H%M%S"),
      "station_callsign": station.callsign,
      "operator": station.operator_name or station.callsign,
      "my_gridsquare": station.grid4,
      "tx_pwr": str(station.tx_power_w),
      "comment": self.comment[:240],
      "country": station.country,
    }


def _adif_field(tag: str, value: str | int | float) -> str:
  v = str(value)
  return f"<{tag}:{len(v)}>{v}"


def record_to_adif_line(rec: QsoRecord, station: StationIdentity) -> str:
  parts = [_adif_field(k, v) for k, v in rec.adif_fields(station).items() if v]
  return " ".join(parts) + " <eor>"


def _norm_band(band: str) -> str:
  return (band or "").strip().lower()


def _utc_qso_date(dt: datetime) -> str:
  return dt.astimezone(timezone.utc).strftime("%Y%m%d")


def _today_utc() -> str:
  return datetime.now(tz=timezone.utc).strftime("%Y%m%d")


class ForgalmiNaplo:
  """Kettős napló: magyar tab (`naplo.txt`) + feltölthető ADIF (`upload.adi`)."""

  def __init__(self, root: Path | None = None, *, station: StationIdentity | None = None) -> None:
    self.root = root or FORGALMI_DIR
    self.root.mkdir(parents=True, exist_ok=True)
    self.station = station or StationIdentity.load()
    self._lock = threading.Lock()
    self._jsonl = AtomicJsonlSink(self.root / "qso.jsonl", power_safe=True)
    # WSJT-X dupe: call + band + mode, csak mai UTC nap (ADIF qso_date fordulónap)
    self._worked_today: set[tuple[str, str, str]] = set()
    self._cache_day = ""
    self._load_worked_cache()

  def _load_worked_cache(self) -> None:
    """Mai UTC napi QSO-k — restart után is (qso.jsonl tail)."""
    today = _today_utc()
    self._cache_day = today
    self._worked_today.clear()
    path = self.root / "qso.jsonl"
    try:
      size = path.stat().st_size
    except OSError:
      return
    try:
      with path.open("rb") as f:
        f.seek(max(0, size - 262144))
        text = f.read().decode("utf-8", errors="replace")
      for line in text.splitlines():
        if not line.strip():
          continue
        try:
          rec = json.loads(line)
        except json.JSONDecodeError:
          continue
        call = _call_key(str(rec.get("call", "")))
        if not call:
          continue
        band = _norm_band(str(rec.get("band", "")))
        mode = str(rec.get("mode", "FT8")).strip().upper() or "FT8"
        if not band:
          continue
        t_raw = rec.get("time_on_iso") or rec.get("time_off_iso")
        if not t_raw:
          continue
        try:
          t_on = datetime.fromisoformat(str(t_raw))
        except ValueError:
          continue
        if t_on.tzinfo is None:
          t_on = t_on.replace(tzinfo=timezone.utc)
        if _utc_qso_date(t_on) != today:
          continue
        self._worked_today.add((call, band, mode))
    except OSError:
      return

  def _ensure_today_cache(self) -> None:
    today = _today_utc()
    if self._cache_day != today:
      self._load_worked_cache()

  def recently_worked(self, call: str, *, band: str, mode: str = "FT8") -> bool:
    """Ma (UTC) már volt QSO ezen a sávon — WSJT-X stílusú dupe."""
    self._ensure_today_cache()
    key = (_call_key(call), _norm_band(band), (mode or "FT8").strip().upper())
    return key in self._worked_today

  def worked_calls_today(self, *, band: str | None = None, mode: str = "FT8") -> set[str]:
    """Mai UTC napon naplózott QSO partnerek — GUI zöld kiemeléshez."""
    self._ensure_today_cache()
    mode_u = (mode or "FT8").strip().upper()
    if band is None:
      return {call for call, _b, m in self._worked_today if m == mode_u}
    nb = _norm_band(band)
    return {call for call, b, m in self._worked_today if b == nb and m == mode_u}

  def append_qso(self, rec: QsoRecord) -> str:
    qso_id = str(uuid.uuid4())
    payload = rec.to_json(station=self.station, qso_id=qso_id)
    hung_line = self._hungarian_line(rec)
    adif_line = record_to_adif_line(rec, self.station) + "\n"
    with self._lock:
      self._jsonl.append(payload)
      self._append_hungarian_line(hung_line)
      self._append_adif_line(adif_line)
      self._ensure_today_cache()
      if _utc_qso_date(rec.time_on) == _today_utc():
        self._worked_today.add(
          (_call_key(rec.call), _norm_band(rec.band), (rec.mode or "FT8").strip().upper())
        )
    return qso_id

  def _hungarian_line(self, rec: QsoRecord) -> str:
    t_on = rec.time_on.astimezone(CET)
    t_off = rec.time_off.astimezone(CET)
    return (
      f"{t_on.strftime('%Y-%m-%d')}\t\t"
      f"{t_on.strftime('%H:%M')}\t{t_off.strftime('%H:%M')}\t"
      f"{rec.call}\t{rec.dial_mhz:.5f}\t{rec.mode}\t"
      f"{rec.rst_sent}\t{rec.rst_rcvd}\t{self.station.tx_power_w}\t"
      f"{rec.partner_name}\t{rec.partner_qth}\n"
    )

  def _append_hungarian_line(self, line: str) -> None:
    path = self.root / "naplo.txt"
    if not path.exists():
      path.write_text(
        "Dátum\t\tKezdés\tVégzés\tHívójel\tFrek.\tÜzemmód\tRST\tRST\tTelj.\tNév\tQTH\n"
        "\t\tCET\tCET\t\tMhz\t\tAdott\tVett\tWatt\t\t\n",
        encoding="utf-8",
      )
    with path.open("a", encoding="utf-8") as f:
      f.write(line)

  def _append_adif_line(self, line: str) -> None:
    path = self.root / "upload.adi"
    if not path.exists():
      path.write_text(
        "ADIF Export from cw-discover FT8 QSO\n"
        "<programid:11>cw-discover\n"
        f"<created:15>{datetime.now(tz=timezone.utc).strftime('%d-%b-%Y %H%M')} UTC\n"
        "<eoh>\n",
        encoding="utf-8",
      )
    with path.open("a", encoding="utf-8") as f:
      f.write(line)

  def _append_hungarian(self, rec: QsoRecord) -> None:
    self._append_hungarian_line(self._hungarian_line(rec))

  def _append_adif(self, rec: QsoRecord) -> None:
    self._append_adif_line(record_to_adif_line(rec, self.station) + "\n")
