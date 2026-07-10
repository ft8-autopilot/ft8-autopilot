"""QSO táblázat sor kiemelés — üzleti logika Qt nélkül."""
from __future__ import annotations

from enum import Enum, auto

from cw_discover.ft8.grid_geo import _call_key
from cw_discover.ft8.decode_meta import message_upper


class QsoRowHighlight(Enum):
  NONE = auto()
  DONE = auto()
  ACTIVE = auto()
  COOLDOWN = auto()


def message_involves_call(calls: tuple[str, ...], msg_up: str, call: str) -> bool:
  cu = _call_key(call)
  for c in calls:
    if c == cu:
      return True
  return cu in msg_up


def resolve_qso_row_highlight(
  *,
  calls: tuple[str, ...],
  msg_up: str,
  active_call: str,
  completed_calls: set[str],
  cooldown_calls: set[str],
) -> QsoRowHighlight:
  for done_call in completed_calls:
    if message_involves_call(calls, msg_up, done_call):
      return QsoRowHighlight.DONE
  if active_call and message_involves_call(calls, msg_up, active_call):
    return QsoRowHighlight.ACTIVE
  for cd in cooldown_calls:
    if message_involves_call(calls, msg_up, cd):
      return QsoRowHighlight.COOLDOWN
  return QsoRowHighlight.NONE
