"""PRO operátor stratégia — meta-elemzés alapú CQ rangsorolás."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache

from cw_discover.ft8.decode_meta import _grid_source_cached, message_stripped, message_upper
from cw_discover.ft8.engine import DecodeReport
from cw_discover.ft8.ft8_protocol import MessageTriplet, is_grid_token
from cw_discover.ft8.grid_geo import _call_key, grid4_upper, station_dist_for_g4
from cw_discover.ft8.home_qth import HomeQth, DEFAULT_HOME


class PriorityMode(str, Enum):
  """WSJT-Z / Auto-FT8 / Fox módok összevonva."""

  BALANCED = "balanced"
  DISTANCE = "distance"
  WEAK_DX = "weak_dx"
  STRONG_FAST = "strong_fast"


@dataclass
class ProOperatorConfig:
  """
  Szakmai FT8 auto-operátor beállítások.

  Források: WSJT-X Fox/Hound guide, WSJT-Z Auto Call, Hamilton Auto FT8,
  KK5JY FT8 automation essay, cw-discover 31k decode elemzés.
  """

  enabled: bool = False
  priority: PriorityMode = PriorityMode.BALANCED
  min_snr: int = -20
  max_snr: int = 3
  min_distance_km: float = 0.0
  prefer_weak_bonus: bool = True
  defer_cq_pick: bool = True
  skip_worked_today: bool = True
  max_retry_cycles: int | None = None
  outbound_fail_cooldown_sec: int | None = None
  preempt_snr_margin_db: float = 3.0

  @classmethod
  def from_dict(cls, data: dict | None) -> ProOperatorConfig:
    if not data:
      return cls()
    pr = str(data.get("priority", "balanced")).lower()
    try:
      mode = PriorityMode(pr)
    except ValueError:
      mode = PriorityMode.BALANCED
    return cls(
      enabled=bool(data.get("enabled", False)),
      priority=mode,
      min_snr=int(data.get("min_snr", -20)),
      max_snr=int(data.get("max_snr", 3)),
      min_distance_km=float(data.get("min_distance_km", 0)),
      prefer_weak_bonus=bool(data.get("prefer_weak_bonus", True)),
      defer_cq_pick=bool(data.get("defer_cq_pick", True)),
      skip_worked_today=bool(data.get("skip_worked_today", True)),
      max_retry_cycles=(
        int(data["max_retry_cycles"]) if data.get("max_retry_cycles") is not None else None
      ),
      outbound_fail_cooldown_sec=(
        int(data["outbound_fail_cooldown_sec"])
        if data.get("outbound_fail_cooldown_sec") is not None
        else None
      ),
      preempt_snr_margin_db=float(data.get("preempt_snr_margin_db", 3.0)),
    )

  def effective_max_retry_cycles(self) -> int:
    if self.max_retry_cycles is not None:
      return max(1, int(self.max_retry_cycles))
    if self.enabled and self.priority == PriorityMode.STRONG_FAST:
      return 2
    return 3

  def effective_outbound_cooldown_sec(self) -> int:
    if self.outbound_fail_cooldown_sec is not None:
      return max(30, int(self.outbound_fail_cooldown_sec))
    if self.enabled and self.priority == PriorityMode.STRONG_FAST:
      return 180
    return 600

  def effective_defer_cq_pick(self) -> bool:
    if self.enabled and self.priority == PriorityMode.STRONG_FAST:
      return False
    return self.defer_cq_pick


@dataclass
class CqCandidate:
  call: str
  grid: str
  audio_hz: float
  snr: int
  distance_km: float | None
  score: float
  message: str
  reason: str
  cycle: str = ""


@dataclass
class ContactIntelCache:
  """Állomás-intel cache — rövidített üzenetekhez (KK5JY: state caching)."""

  grids: dict[str, str] = field(default_factory=dict)
  last_snr: dict[str, int] = field(default_factory=dict)
  last_distance_km: dict[str, float] = field(default_factory=dict)

  def note_decode(self, call: str, grid: str, snr: int, distance_km: float | None) -> None:
    c = _call_key(call)
    if grid:
      self.grids[c] = grid4_upper(grid)
    self.last_snr[c] = snr
    if distance_km is not None:
      self.last_distance_km[c] = distance_km

  def grid_for(self, call: str) -> str:
    return self.grids.get(_call_key(call), "")

  def distance_for(self, call: str) -> float | None:
    return self.last_distance_km.get(_call_key(call))


def _directed_cq_match(message: str, home: HomeQth) -> bool:
  """CQ DX / CQ EU — irányított CQ szűrés (Fox guide #9)."""
  return _directed_cq_match_cached(
    message_upper(message),
    home.country,
    (home.grid or "")[:1],
  )


@lru_cache(maxsize=2048)
def _directed_cq_match_cached(message_upper: str, home_country: str, home_grid0: str) -> bool:
  parts = message_upper.split()
  if len(parts) < 2 or parts[0] != "CQ":
    return True
  mod = parts[1]
  if mod == "DX":
    return True
  if mod in ("EU", "EUROPE"):
    return home_country in ("Magyarország", "Hungary") or home_grid0 == "J"
  return True


def _score_candidate_core(
  *,
  call: str,
  report: DecodeReport,
  grid: str,
  distance_km: float | None,
  worked: bool,
  config: ProOperatorConfig,
  home: HomeQth,
  is_cq: bool,
) -> CqCandidate | None:
  """Közös PRO pontozás — CQ és bejövő hívás."""
  snr = int(report.snr)
  reasons: list[str] = []

  if snr < config.min_snr or snr > config.max_snr:
    return None
  if is_cq and not _directed_cq_match(report.message, home):
    return None
  if worked and config.skip_worked_today:
    return None
  dist = distance_km
  if config.min_distance_km > 0 and (dist is None or dist < config.min_distance_km):
    return None

  score = 0.0
  mode = config.priority

  if mode == PriorityMode.DISTANCE:
    score = float(dist or 0)
    reasons.append(f"táv {dist or '?'} km")
  elif mode == PriorityMode.WEAK_DX:
    weak = max(0.0, -float(snr))
    score = weak * 80.0 + float(dist or 0) * 0.8
    if dist and dist > 2000:
      score += 400
      reasons.append("táv DX")
    if snr < -10:
      reasons.append(f"gyenge SNR {snr:+d}")
  elif mode == PriorityMode.STRONG_FAST:
    score = float(snr) * 15.0
    reasons.append("erős = gyors QSO")
  else:
    score = float(dist or 250) * 0.4
    if config.prefer_weak_bonus and snr < 0:
      score += (-snr) * 12.0
      reasons.append("gyenge→messzi?")
    if snr > 0:
      score -= snr * 6.0
    if grid:
      score += 80.0

  if is_cq and worked:
    return None
  if not reasons:
    reasons.append("bejövő" if not is_cq else mode.value)

  return CqCandidate(
    call=call,
    grid=grid4_upper(grid) if grid else "",
    audio_hz=float(report.audio_hz),
    snr=snr,
    distance_km=dist,
    score=score,
    message=report.message,
    reason=", ".join(reasons),
    cycle=report.cycle,
  )


def score_cq_candidate(
  *,
  report: DecodeReport,
  triplet: MessageTriplet,
  grid: str,
  distance_km: float | None,
  worked: bool,
  config: ProOperatorConfig,
  home: HomeQth | None = None,
) -> CqCandidate | None:
  """CQ jelölt pontozása — magasabb = értékesebb QSO."""
  home = home or DEFAULT_HOME
  return _score_candidate_core(
    call=triplet.call_b,
    report=report,
    grid=grid,
    distance_km=distance_km,
    worked=worked,
    config=config,
    home=home,
    is_cq=True,
  )


def score_incoming_candidate(
  *,
  report: DecodeReport,
  triplet: MessageTriplet,
  grid: str,
  distance_km: float | None,
  worked: bool,
  config: ProOperatorConfig,
  home: HomeQth | None = None,
) -> CqCandidate | None:
  """Bejövő hívás (CALL ME …) — CQ üzem bufferhez."""
  home = home or DEFAULT_HOME
  return _score_candidate_core(
    call=triplet.call_a,
    report=report,
    grid=grid,
    distance_km=distance_km,
    worked=worked,
    config=config,
    home=home,
    is_cq=False,
  )


def pick_best_cq(candidates: list[CqCandidate]) -> CqCandidate | None:
  if not candidates:
    return None
  return max(candidates, key=lambda c: c.score)


def geo_distance_for_cq(report: DecodeReport, home: HomeQth | None) -> tuple[str, float | None]:
  home = home or DEFAULT_HOME
  return _geo_distance_cached(
    message_stripped(report.message),
    round(home.lat, 3),
    round(home.lon, 3),
  )


@lru_cache(maxsize=4096)
def _geo_distance_cached(message: str, home_lat: float, home_lon: float) -> tuple[str, float | None]:
  grid, _source = _grid_source_cached(message)
  if not grid:
    return "", None
  g = grid4_upper(grid)
  dist = station_dist_for_g4(g, home_lat, home_lon)
  return g, dist
