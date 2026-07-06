#!/usr/bin/env python3
"""Egy tick élő FT8 felügyelet — állapot, beavatkozás, jelentés."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cw_discover.paths import FORGALMI_LIVE, LOG_DIR  # noqa: E402
from cw_discover.ft8.decode_meta import daily_decodes_jsonl  # noqa: E402

LIVE = FORGALMI_LIVE
GUI_STATUS = LIVE / "gui_status.json"
SAFETY = LIVE / "safety_state.json"
OPERATOR_IN = LIVE / "operator_in.txt"
SUP_LOG = LIVE / "supervisor.log"
GUI_LOG = LIVE / "gui_nohup.log"

EXPECTED_BAND = "20m"
EXPECTED_DIAL = 14.074
DIAL_TOL = 0.002
STALL_SEC = 300  # 5 perc dekód nélkül → RX újraindítás


def _log(msg: str) -> None:
  LIVE.mkdir(parents=True, exist_ok=True)
  ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
  line = f"[{ts}] {msg}"
  print(line, flush=True)
  with SUP_LOG.open("a", encoding="utf-8") as fh:
    fh.write(line + "\n")


def _read_json(path: Path) -> dict:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}


def _send(*cmds: str) -> None:
  OPERATOR_IN.write_text("\n".join(cmds) + "\n", encoding="utf-8")


def _decode_count_file() -> int:
  path = daily_decodes_jsonl(LOG_DIR)
  if not path.exists():
    return 0
  n = 0
  with path.open(encoding="utf-8", errors="replace") as fh:
    for line in fh:
      if line.strip():
        n += 1
  return n


def _recent_gui_errors() -> list[str]:
  if not GUI_LOG.exists():
    return []
  try:
    tail = GUI_LOG.read_text(encoding="utf-8", errors="replace")[-8000:]
  except OSError:
    return []
  errs = []
  for ln in tail.splitlines():
    if any(x in ln for x in ("Traceback", "Error", "HIBA", "AttributeError", "NameError")):
      errs.append(ln[:120])
  return errs[-3:]


def _load_stall_state() -> dict:
  p = LIVE / "supervisor_stall.json"
  return _read_json(p)


def _save_stall_state(data: dict) -> None:
  LIVE.mkdir(parents=True, exist_ok=True)
  (LIVE / "supervisor_stall.json").write_text(json.dumps(data), encoding="utf-8")


def run_tick() -> dict:
  actions: list[str] = []
  warnings: list[str] = []
  st = _read_json(GUI_STATUS)
  safety = _read_json(SAFETY)

  if not st:
    warnings.append("gui_status.json hiányzik vagy üres")
    _send("START_RX", "PRO_ON", "PTT_ON", f"BAND {EXPECTED_BAND}")
    actions.append("operator_in: START_RX PRO_ON PTT_ON BAND")
    report = {"ok": False, "actions": actions, "warnings": warnings}
    _log(f"TICK ok={report['ok']} actions={actions} warn={warnings}")
    return report

  band = st.get("band", "")
  dial = float(st.get("dial_mhz", 0) or 0)
  rx = bool(st.get("rx_running"))
  ptt = bool(st.get("ptt_armed"))
  pro = bool(st.get("pro_operator"))
  tripped = bool(safety.get("tripped")) or bool(st.get("safety_tripped"))
  tx_err = str(st.get("last_tx_error", ""))
  phase = st.get("qso_phase", "?")
  partner = st.get("qso_partner", "")
  dec_gui = int(st.get("decode_count", 0))
  tx_active = bool(st.get("tx_active"))
  ptt_ok = bool(st.get("ptt_serial_ok", True))
  last_msg = str(st.get("last_message", ""))[:60]

  cmds: list[str] = []
  if tripped:
    warnings.append(f"BIZTONSÁGI TILTÁS: {safety.get('reason') or st.get('safety_reason')}")
  else:
    if band != EXPECTED_BAND or abs(dial - EXPECTED_DIAL) > DIAL_TOL:
      cmds.append(f"BAND {EXPECTED_BAND}")
      actions.append(f"sáv javítás → {EXPECTED_BAND}")
    if not rx:
      cmds.append("START_RX")
      actions.append("RX újraindítás")
    if not pro:
      cmds.append("PRO_ON")
      actions.append("PRO be")
    if not ptt:
      cmds.append("PTT_ON")
      actions.append("PTT fegyverezés")

  if cmds:
    _send(*cmds)

  if tx_err:
    warnings.append(f"TX hiba: {tx_err}")
  if not ptt_ok:
    warnings.append("ESP32 / PTT soros nem OK")

  # Dekód stall detektálás
  file_dec = _decode_count_file()
  stall = _load_stall_state()
  now = time.time()
  prev_ts = float(stall.get("ts", 0))
  prev_count = int(stall.get("file_dec", 0))
  if file_dec > prev_count:
    stall = {"ts": now, "file_dec": file_dec}
  elif rx and not tripped and (now - prev_ts) > STALL_SEC:
    warnings.append(f"dekód stall {int(now - prev_ts)}s — RX újraindítás")
    _send("START_RX", f"BAND {EXPECTED_BAND}", "PTT_ON")
    actions.append("stall → START_RX")
    stall = {"ts": now, "file_dec": file_dec}
  _save_stall_state(stall)

  gui_errs = _recent_gui_errors()
  if gui_errs:
    warnings.append("GUI log hiba: " + gui_errs[-1])

  ok = not tripped and rx and ptt and band == EXPECTED_BAND and not tx_err
  report = {
    "ok": ok,
    "band": band,
    "dial_mhz": dial,
    "rx": rx,
    "ptt": ptt,
    "phase": phase,
    "partner": partner,
    "tx_active": tx_active,
    "decode_gui": dec_gui,
    "decode_file": file_dec,
    "last_message": last_msg,
    "actions": actions,
    "warnings": warnings,
  }
  _log(
    f"TICK ok={ok} {band}@{dial:.3f} rx={rx} ptt={ptt} phase={phase} "
    f"dec={dec_gui}/{file_dec} tx={tx_active} last={last_msg!r} "
    f"actions={actions or '-'} warn={warnings or '-'}"
  )
  return report


if __name__ == "__main__":
  r = run_tick()
  print("REPORT_JSON " + json.dumps(r, ensure_ascii=False), flush=True)
