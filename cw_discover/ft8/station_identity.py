"""Állomás azonosító — hívójel, lokátor, teljesítmény."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cw_discover.ft8.grid_geo import _call_key, grid4_upper
from cw_discover.ft8.pro_operator import ProOperatorConfig
from cw_discover.paths import FORGALMI_DIR, STATION_FILE

CQ_WAIT_PERIOD_CHOICES = (1, 3, 5, 7, 9)


def normalize_cq_wait_periods(n: int) -> int:
  """Érvényes páratlan FT8 periódus — 1, 3, 5, 7 vagy 9."""
  if n in CQ_WAIT_PERIOD_CHOICES:
    return n
  return min(CQ_WAIT_PERIOD_CHOICES, key=lambda x: abs(x - n))


@dataclass
class StationIdentity:
  callsign: str
  grid: str
  operator_name: str = ""
  operator_legal_name: str = ""
  qth: str = "Example City"
  qth_detail: str = ""
  country: str = "Magyarország"
  tx_power_w: int = 5
  ptt_port: str = "/dev/ttyUSB0"
  ptt_baud: int = 115200
  tx_audio_device: str = "pulse"
  cq_min_snr: int = -18
  cq_repeat_cycles: int = 3  # várakozási periódusok CQ-k között (1, 3, 5, 7, 9)
  pro: ProOperatorConfig = field(default_factory=ProOperatorConfig)

  @property
  def grid4(self) -> str:
    return grid4_upper(self.grid)

  @classmethod
  def load(cls, path: Path | None = None) -> StationIdentity:
    p = path or STATION_FILE
    if not p.exists():
      return cls(callsign="N0CALL", grid="JN96", operator_name="")
    data = json.loads(p.read_text(encoding="utf-8"))
    return cls(
      callsign=_call_key(str(data.get("callsign", "N0CALL"))),
      grid=grid4_upper(str(data.get("grid", "JN96"))),
      operator_name=str(data.get("operator_name", "")),
      operator_legal_name=str(data.get("operator_legal_name", "")),
      qth=str(data.get("qth", "Example City")),
      qth_detail=str(data.get("qth_detail", "")),
      country=str(data.get("country", "Magyarország")),
      tx_power_w=int(data.get("tx_power_w", 5)),
      ptt_port=str(data.get("ptt_port", "/dev/ttyUSB0")),
      ptt_baud=int(data.get("ptt_baud", 115200)),
      tx_audio_device=str(data.get("tx_audio_device", "pulse")),
      cq_min_snr=int(data.get("cq_min_snr", -18)),
      cq_repeat_cycles=normalize_cq_wait_periods(int(data.get("cq_repeat_cycles", 3))),
      pro=ProOperatorConfig.from_dict(data.get("pro_operator")),
    )

  def save_cq_wait_periods(self, periods: int) -> None:
    """CQ várakozási periódusok mentése station.json-ba."""
    periods = normalize_cq_wait_periods(periods)
    p = STATION_FILE
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data["cq_repeat_cycles"] = periods
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    self.cq_repeat_cycles = periods

  def save_pro_enabled(self, enabled: bool) -> None:
    """PRO operátor kapcsoló mentése station.json-ba."""
    p = STATION_FILE
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    block = data.get("pro_operator") or {}
    block["enabled"] = enabled
    data["pro_operator"] = block
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    self.pro.enabled = enabled
