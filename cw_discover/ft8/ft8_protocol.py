"""FT8 üzenet-feldolgozás — QSO állapotgéphez (WSJT-X / PyFT8 kompatibilis)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from cw_discover.ft8.callsign import CQ_MODIFIERS, is_callsign, is_cq_modifier, valid_remote_call
from cw_discover.ft8.decode_meta import _message_upper_cached, message_stripped
from cw_discover.ft8.grid_geo import GRID4_RE, REPORT_RE, grid4_upper

# Kompatibilitás — régi importok
__all__ = [
  "CQ_MODIFIERS",
  "MessageTriplet",
  "message_triplet",
  "normalize_directed_call_order",
  "is_report",
  "is_r_report",
  "is_rrr",
  "is_rr73",
  "is_73",
  "is_grid_token",
  "snr_report_text",
  "rst_from_report_token",
  "valid_remote_call",
]


@dataclass(frozen=True)
class MessageTriplet:
  call_a: str
  call_b: str
  third: str

  @property
  def is_cq(self) -> bool:
    return self.call_a == "CQ"


def message_triplet(message: str) -> MessageTriplet | None:
  return _message_triplet_cached(message_stripped(message))


@lru_cache(maxsize=4096)
def _message_triplet_cached(message: str) -> MessageTriplet | None:
  parts = [p for p in _message_upper_cached(message).split() if p]
  if not parts:
    return None
  if parts[0] == "CQ":
    if len(parts) == 2:
      return MessageTriplet("CQ", parts[1], "")
    if parts[1] in CQ_MODIFIERS or is_cq_modifier(parts[1]):
      call = parts[2] if len(parts) > 2 else ""
      grid = parts[3] if len(parts) > 3 else ""
      return MessageTriplet("CQ", call, grid)
    return MessageTriplet("CQ", parts[1], parts[2] if len(parts) > 2 else "")
  if len(parts) >= 3:
    return MessageTriplet(parts[0], parts[1], parts[2])
  return None


def normalize_directed_call_order(
  message: str,
  me: str,
  *,
  my_grid4: str = "",
) -> str:
  """
  PyFT8/line-in fordított call sorrend javítása: N0CALL REMOTE … → REMOTE N0CALL …

  Csak irányított QSO üzenetek (grid/report/73) — CQ és már helyes sorrend érintetlen.
  """
  me = me.strip().upper()
  if not me:
    return message_stripped(message)
  triplet = message_triplet(message)
  if triplet is None or triplet.is_cq:
    return message_stripped(message)
  if triplet.call_b == me:
    return message_stripped(message)
  if triplet.call_a != me or not valid_remote_call(triplet.call_b):
    return message_stripped(message)
  third = triplet.third
  g4 = grid4_upper(my_grid4) if my_grid4 else ""
  if g4 and is_grid_token(third) and grid4_upper(third) == g4:
    return message_stripped(message)
  if is_report(third) or is_r_report(third) or is_grid_token(third):
    return f"{triplet.call_b} {me} {third}"
  if is_rr73(third) or is_73(third) or is_rrr(third):
    return f"{triplet.call_b} {me} {third}"
  return message_stripped(message)


def is_report(token: str) -> bool:
  return _is_report_cached(token.upper())


@lru_cache(maxsize=4096)
def _is_report_cached(t: str) -> bool:
  if t in ("73", "RR73", "RRR"):
    return False
  return "+" in t or "-" in t


def is_r_report(token: str) -> bool:
  return _is_r_report_cached(token.upper())


@lru_cache(maxsize=512)
def _is_r_report_cached(t: str) -> bool:
  return _is_report_cached(t) and t.startswith("R") and t not in ("RRR", "RR73")


def is_rrr(token: str) -> bool:
  return _is_rrr_cached(token.upper())


@lru_cache(maxsize=512)
def _is_rrr_cached(t: str) -> bool:
  return t == "RRR"


def is_rr73(token: str) -> bool:
  return _is_rr73_cached(token.upper())


@lru_cache(maxsize=512)
def _is_rr73_cached(t: str) -> bool:
  return t == "RR73"


def is_73(token: str) -> bool:
  return _is_73_cached(token.upper())


@lru_cache(maxsize=512)
def _is_73_cached(t: str) -> bool:
  return t == "73"


def is_grid_token(token: str) -> bool:
  return _is_grid_token_cached(token.upper())


@lru_cache(maxsize=4096)
def _is_grid_token_cached(t: str) -> bool:
  if is_report(t) or is_73(t) or is_rr73(t) or is_rrr(t):
    return False
  tok = t.strip("<>")
  if GRID4_RE.match(tok):
    return True
  return len(tok) >= 6 and GRID4_RE.match(tok[:4]) and tok[4:6].isalpha()


def snr_report_text(snr: int) -> str:
  return _snr_report_cached(int(snr))


@lru_cache(maxsize=128)
def _snr_report_cached(snr: int) -> str:
  return f"{snr:+03d}"


def rst_from_report_token(token: str) -> str:
  return _rst_from_report_cached(token.upper())


@lru_cache(maxsize=4096)
def _rst_from_report_cached(t: str) -> str:
  body = t.lstrip("R")
  if body in ("73", "RR73", "RRR"):
    return ""
  m = re.match(r"^([+-]?\d{1,2})$", body)
  if m:
    v = int(m.group(1))
    return f"{v:+03d}"
  return t[:3]
