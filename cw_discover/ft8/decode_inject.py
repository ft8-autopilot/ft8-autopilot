"""Szintetikus dekód injektálás — operátor parancs / teszt."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from cw_discover.ft8.engine import DecodeReport
from cw_discover.ft8.ft8_slot import cycle_key_at


def parse_inject_decode_command(cmd: str) -> DecodeReport | None:
  """
  INJECT_DECODE N0CALL DK7ZT JO30
  INJECT_DECODE N0CALL DK7ZT JO30 -8 1867
  """
  rest = cmd[len("INJECT_DECODE ") :].strip()
  if not rest:
    return None
  parts = rest.upper().split()
  snr = -10
  hz = 1867
  if len(parts) >= 2 and _is_int(parts[-1]) and _is_int(parts[-2]):
    hz = int(parts[-1])
    snr = int(parts[-2])
    parts = parts[:-2]
  elif len(parts) >= 1 and _is_int(parts[-1]):
    snr = int(parts[-1])
    parts = parts[:-1]
  if len(parts) < 2:
    return None
  message = " ".join(parts)
  now = time.time()
  return DecodeReport(
    cycle=cycle_key_at(),
    snr=snr,
    dt=0.1,
    audio_hz=hz,
    rf_khz=7074.0,
    message=message,
    time_received=now,
    cycle_start_utc=datetime.now(tz=timezone.utc).isoformat(),
    dsp={"inject": True},
    audio={},
  )


def _is_int(token: str) -> bool:
  try:
    int(token)
    return True
  except ValueError:
    return False
