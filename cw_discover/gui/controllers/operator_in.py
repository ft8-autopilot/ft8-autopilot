"""operator_in.txt parancsok — tiszta parse réteg (GUI-tól független)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from cw_discover.ft8.pro_operator import PriorityMode


class OperatorCmdKind(Enum):
  BAND = auto()
  DIAL = auto()
  PTT_ON = auto()
  PTT_OFF = auto()
  PRO_ON = auto()
  PRO_OFF = auto()
  CQ_MODE_ON = auto()
  CQ_MODE_OFF = auto()
  PRO_PRIORITY = auto()
  MAP_ON = auto()
  MAP_OFF = auto()
  CQ_WAIT = auto()
  PTT_PULSE = auto()
  START_RX = auto()
  TX_TEST = auto()
  ABORT_QSO = auto()
  SAFETY_RESUME = auto()
  SAFETY_UNLOCK = auto()
  ESP_SHUTDOWN = auto()
  ERROR_INJECT_ALL = auto()
  ERROR_INJECT = auto()
  CALL = auto()
  INJECT_DECODE = auto()


@dataclass(frozen=True)
class OperatorCommand:
  kind: OperatorCmdKind
  arg1: str = ""
  arg2: str = ""
  arg3: str = ""
  arg4: str = ""
  raw: str = ""


_PRIORITY_ALIASES: dict[str, PriorityMode] = {
  "balanced": PriorityMode.BALANCED,
  "kiegyensúlyozott": PriorityMode.BALANCED,
  "distance": PriorityMode.DISTANCE,
  "távolság": PriorityMode.DISTANCE,
  "weak_dx": PriorityMode.WEAK_DX,
  "gyenge": PriorityMode.WEAK_DX,
  "strong_fast": PriorityMode.STRONG_FAST,
  "gyors": PriorityMode.STRONG_FAST,
}


def parse_priority_mode(raw: str) -> PriorityMode:
  key = (raw or "").strip().lower()
  mode = _PRIORITY_ALIASES.get(key)
  if mode is not None:
    return mode
  try:
    return PriorityMode(key)
  except ValueError:
    return PriorityMode.BALANCED


def normalize_band_token(token: str) -> str:
  band = token.strip().lower()
  if band.isdigit():
    return f"{band}m"
  return band


def parse_operator_line(line: str) -> OperatorCommand | None:
  raw = line.strip()
  if not raw:
    return None
  cmd = raw.upper()

  if cmd.startswith("BAND "):
    parts = cmd.split()
    if len(parts) >= 2:
      return OperatorCommand(OperatorCmdKind.BAND, arg1=normalize_band_token(parts[1]), raw=raw)
    return None

  if cmd.startswith("DIAL "):
    parts = cmd.split()
    if len(parts) >= 2:
      return OperatorCommand(OperatorCmdKind.DIAL, arg1=parts[1], raw=raw)
    return None

  simple = {
    "PTT_ON": OperatorCmdKind.PTT_ON,
    "PTT_OFF": OperatorCmdKind.PTT_OFF,
    "PRO_ON": OperatorCmdKind.PRO_ON,
    "PRO_OFF": OperatorCmdKind.PRO_OFF,
    "CQ_MODE_ON": OperatorCmdKind.CQ_MODE_ON,
    "CQ_MODE_OFF": OperatorCmdKind.CQ_MODE_OFF,
    "MAP_ON": OperatorCmdKind.MAP_ON,
    "MAP_OFF": OperatorCmdKind.MAP_OFF,
    "START_RX": OperatorCmdKind.START_RX,
    "TX_TEST": OperatorCmdKind.TX_TEST,
    "ABORT_QSO": OperatorCmdKind.ABORT_QSO,
    "SAFETY_RESUME": OperatorCmdKind.SAFETY_RESUME,
    "RESUME_ESP": OperatorCmdKind.SAFETY_RESUME,
    "SAFETY_UNLOCK": OperatorCmdKind.SAFETY_UNLOCK,
    "ESP_SHUTDOWN": OperatorCmdKind.ESP_SHUTDOWN,
    "ERROR_INJECT_ALL": OperatorCmdKind.ERROR_INJECT_ALL,
  }
  if cmd in simple:
    return OperatorCommand(simple[cmd], raw=raw)

  if cmd.startswith("PRO_PRIORITY "):
    arg = cmd.split(maxsplit=1)[1] if " " in cmd else ""
    return OperatorCommand(OperatorCmdKind.PRO_PRIORITY, arg1=arg, raw=raw)

  if cmd.startswith("CQ_WAIT "):
    parts = cmd.split()
    if len(parts) >= 2:
      return OperatorCommand(OperatorCmdKind.CQ_WAIT, arg1=parts[1], raw=raw)
    return None

  if cmd.startswith("PTT_PULSE"):
    parts = cmd.split()
    secs = parts[1] if len(parts) > 1 else "2.0"
    return OperatorCommand(OperatorCmdKind.PTT_PULSE, arg1=secs, raw=raw)

  if cmd.startswith("ERROR_INJECT "):
    return OperatorCommand(OperatorCmdKind.ERROR_INJECT, arg1=raw[13:].strip(), raw=raw)

  if cmd.startswith("CALL "):
    parts = cmd.split()
    if len(parts) >= 2:
      return OperatorCommand(
        OperatorCmdKind.CALL,
        arg1=parts[1],
        arg2=parts[2] if len(parts) > 2 else "1867.0",
        arg3=parts[3] if len(parts) > 3 else "",
        arg4=parts[4] if len(parts) > 4 else "-15",
        raw=raw,
      )
    return None

  if cmd.startswith("INJECT_DECODE "):
    return OperatorCommand(OperatorCmdKind.INJECT_DECODE, arg1=raw, raw=raw)

  return None


def parse_operator_batch(text: str) -> list[OperatorCommand]:
  out: list[OperatorCommand] = []
  for line in text.splitlines():
    parsed = parse_operator_line(line)
    if parsed is not None:
      out.append(parsed)
  return out
