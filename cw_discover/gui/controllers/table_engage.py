"""Dekód táblázat sor → kézi engage kérés (Qt nélkül)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from cw_discover.ft8.ft8_protocol import is_report, message_triplet, valid_remote_call
from cw_discover.ft8.grid_geo import _call_key


class TableEngageReject(Enum):
  EMPTY_MESSAGE = auto()
  UNPARSEABLE = auto()
  NO_REMOTE = auto()
  SELF_ONLY = auto()
  INVALID_HZ = auto()


@dataclass(frozen=True)
class TableEngageRequest:
  call: str
  audio_hz: float
  rx_report: str = ""
  rx_snr: int = 0


@dataclass(frozen=True)
class TableEngageResult:
  ok: bool
  request: TableEngageRequest | None = None
  reject: TableEngageReject | None = None
  detail: str = ""


def parse_audio_hz_column(text: str) -> float | None:
  """Audio Hz oszlop: „397 (7074.000 kHz)” → 397.0."""
  t = (text or "").strip()
  if not t:
    return None
  head = t.split()[0]
  try:
    hz = float(head)
  except ValueError:
    return None
  return hz if hz > 0 else None


def parse_snr_column(text: str) -> int:
  t = (text or "").strip().lstrip("+")
  try:
    return int(t)
  except ValueError:
    return 0


def resolve_remote_call(message: str, my_callsign: str) -> str | None:
  """Távoli hívójel a dekód üzenetből — CQ, irányított QSO, fallback."""
  me = _call_key(my_callsign)
  triplet = message_triplet(message)
  if triplet is None:
    return None
  if triplet.is_cq:
    call = _call_key(triplet.call_b)
    return call if valid_remote_call(call) else None
  a = _call_key(triplet.call_a)
  b = _call_key(triplet.call_b)
  if a == me and valid_remote_call(b):
    return b
  if b == me and valid_remote_call(a):
    return a
  for c in (a, b):
    if c != me and valid_remote_call(c):
      return c
  return None


def parse_table_engage(
  *,
  message: str,
  my_callsign: str,
  audio_hz_text: str,
  snr_text: str = "0",
) -> TableEngageResult:
  msg = (message or "").strip()
  if not msg:
    return TableEngageResult(False, reject=TableEngageReject.EMPTY_MESSAGE)
  hz = parse_audio_hz_column(audio_hz_text)
  if hz is None:
    return TableEngageResult(False, reject=TableEngageReject.INVALID_HZ, detail=audio_hz_text)
  triplet = message_triplet(msg)
  if triplet is None:
    return TableEngageResult(False, reject=TableEngageReject.UNPARSEABLE)
  remote = resolve_remote_call(msg, my_callsign)
  if not remote:
    return TableEngageResult(False, reject=TableEngageReject.NO_REMOTE)
  me = _call_key(my_callsign)
  if remote == me:
    return TableEngageResult(False, reject=TableEngageReject.SELF_ONLY)

  snr = parse_snr_column(snr_text)
  rx_report = ""
  if triplet.third and is_report(triplet.third) and not triplet.is_cq:
    if triplet.call_a == me or triplet.call_b == me:
      rx_report = triplet.third

  return TableEngageResult(
    ok=True,
    request=TableEngageRequest(call=remote, audio_hz=hz, rx_report=rx_report, rx_snr=snr),
  )
