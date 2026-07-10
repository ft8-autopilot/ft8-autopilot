"""tx.log integritás — null bájtok / sérült sorok javítása induláskor."""
from __future__ import annotations

from pathlib import Path

from cw_discover.paths import TX_LOG


def repair_tx_log(path: Path | None = None) -> int:
  """
  Null bájtok és nem nyomtatható sorok eltávolítása.
  Vissza: eltávolított bájtok száma (0 ha nem volt teendő).
  """
  log_path = path or TX_LOG
  if not log_path.is_file():
    return 0
  try:
    raw = log_path.read_bytes()
  except OSError:
    return 0
  if not raw:
    return 0
  nul = raw.count(b"\x00")
  if nul == 0:
    return 0
  cleaned = raw.replace(b"\x00", b"")
  lines = []
  for line in cleaned.splitlines():
    try:
      text = line.decode("utf-8", errors="replace").strip()
    except Exception:
      continue
    if text:
      lines.append(text)
  try:
    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
  except OSError:
    return 0
  return nul
