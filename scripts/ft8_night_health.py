#!/usr/bin/env python3
"""FT8 éjszakai egészségellenőrzés — csak kritikus hibánál restart."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cw_discover.ft8.decode_meta import time_iso_utc
from cw_discover.ft8.json_fast import dumps_compact
from cw_discover.paths import FORGALMI_LIVE, TX_LOG

LIVE = FORGALMI_LIVE
GUI_STATUS = LIVE / "gui_status.json"
STATE_PATH = LIVE / "night_watch_state.json"
WATCH_LOG = LIVE / "night_watch.log"
START_SCRIPT = ROOT / "scripts" / "start_overnight_40m.sh"
OPERATOR_IN = LIVE / "operator_in.txt"

EXPECTED_BAND = "40m"
EXPECTED_DIAL = 7.074
DIAL_TOL = 0.002
EXPECTED_CQ_ONLY = False
EXPECTED_PRO_PRIORITY = "balanced"
EXPECTED_CQ_WAIT = 1
EXPECTED_MAP = False

STALE_STATUS_SEC = 180
STUCK_TX_SEC = 90
FLAT_DECODE_CHECKS = 3  # 3×30 perc dekód nélkül → restart
PENDING_RESTART_MAX_SEC = 900  # max 15 perc várakozás aktív QSO-ra
QSO_BUSY_PHASES = frozenset({"active", "closing"})


def qso_in_progress(st: dict) -> bool:
  phase = str(st.get("qso_phase", "idle"))
  partner = str(st.get("qso_partner", "")).strip()
  return phase in QSO_BUSY_PHASES and bool(partner)


def is_critical_restart(reasons: list[str]) -> bool:
  """Azonnali restart — QSO várakozás nélkül."""
  joined = " | ".join(reasons)
  if "run_ft8_gui.py nem fut" in joined:
    return True
  if "hiányzik vagy sérült" in joined:
    return True
  if "safety_tripped" in joined:
    return True
  if "gui_status elavult" in joined:
    # 10+ perc teljes csend — akkor is restart, QSO-t is fel kell adni
    for r in reasons:
      if "elavult" in r:
        try:
          sec = float(r.split("(")[1].split("s")[0])
          if sec > 600:
            return True
        except (IndexError, ValueError):
          return True
  return False


def pending_restart_expired(state: dict) -> bool:
  raw = state.get("pending_restart_since")
  if not raw:
    return False
  try:
    ts = datetime.fromisoformat(str(raw))
    if ts.tzinfo is None:
      ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() > PENDING_RESTART_MAX_SEC
  except ValueError:
    return True


def log(msg: str) -> None:
  LIVE.mkdir(parents=True, exist_ok=True)
  ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  line = f"[{ts}] {msg}"
  print(line, flush=True)
  with WATCH_LOG.open("a", encoding="utf-8") as f:
    f.write(line + "\n")


_status_cache: dict = {}
_status_mtime: float = -1.0
_tx_ts_cache: tuple[float, str] = (-1.0, "")


def load_status() -> dict:
  global _status_cache, _status_mtime
  try:
    mtime = GUI_STATUS.stat().st_mtime
    if mtime == _status_mtime:
      return _status_cache
    _status_cache = json.loads(GUI_STATUS.read_text(encoding="utf-8"))
    _status_mtime = mtime
    return _status_cache
  except (json.JSONDecodeError, OSError):
    return {}


def status_age_sec(st: dict) -> float | None:
  raw = st.get("time_utc")
  if not raw:
    return None
  try:
    ts = datetime.fromisoformat(str(raw))
    if ts.tzinfo is None:
      ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()
  except ValueError:
    return None


def gui_running() -> bool:
  try:
    r = subprocess.run(
      ["pgrep", "-f", "run_ft8_gui.py"],
      capture_output=True,
      text=True,
      timeout=5,
    )
    return r.returncode == 0
  except (subprocess.TimeoutExpired, OSError):
    return False


def bridge_running() -> bool:
  try:
    r = subprocess.run(
      ["pgrep", "-f", "ft8_live_bridge.py"],
      capture_output=True,
      text=True,
      timeout=5,
    )
    return r.returncode == 0
  except (subprocess.TimeoutExpired, OSError):
    return False


def last_tx_age_sec() -> float | None:
  global _tx_ts_cache
  try:
    st = TX_LOG.stat()
    mtime = st.st_mtime
    if mtime == _tx_ts_cache[0] and _tx_ts_cache[1]:
      ts = datetime.fromisoformat(_tx_ts_cache[1])
      if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
      return (datetime.now(timezone.utc) - ts).total_seconds()
    size = st.st_size
    with TX_LOG.open("rb") as f:
      f.seek(max(0, size - 8192))
      chunk = f.read().decode("utf-8", errors="replace")
    lines = chunk.splitlines()
  except OSError:
    return None
  for line in reversed(lines):
    if "TX_START" not in line and "TX_OK" not in line:
      continue
    try:
      iso = line[:26]
      ts = datetime.fromisoformat(iso)
      if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
      _tx_ts_cache = (mtime, iso)
      return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
      continue
  return None


def load_state() -> dict:
  try:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))
  except (json.JSONDecodeError, OSError):
    return {}


def save_state(**fields) -> None:
  st = load_state()
  st.update(fields)
  st["updated_utc"] = time_iso_utc(time.time())
  STATE_PATH.write_text(dumps_compact(st) + "\n", encoding="utf-8")


def soft_fix(st: dict) -> list[str]:
  cmds: list[str] = []
  band = str(st.get("band", ""))
  dial = float(st.get("dial_mhz") or 0)
  if band != EXPECTED_BAND:
    cmds.append(f"BAND {EXPECTED_BAND}")
  if abs(dial - EXPECTED_DIAL) > DIAL_TOL:
    cmds.append(f"DIAL {EXPECTED_DIAL:.3f}")
  if bool(st.get("cq_only_mode")) != EXPECTED_CQ_ONLY:
    cmds.append("CQ_MODE_OFF")
  if str(st.get("pro_priority", "")).lower() != EXPECTED_PRO_PRIORITY:
    cmds.append(f"PRO_PRIORITY {EXPECTED_PRO_PRIORITY}")
  if bool(st.get("map_visible", True)) != EXPECTED_MAP:
    cmds.append("MAP_OFF")
  if int(st.get("cq_wait_periods") or 0) != EXPECTED_CQ_WAIT:
    cmds.append(f"CQ_WAIT {EXPECTED_CQ_WAIT}")
  if not st.get("rx_running"):
    cmds.append("START_RX")
  if not st.get("pro_operator"):
    cmds.append("PRO_ON")
  if not st.get("ptt_armed"):
    cmds.append("PTT_ON")
  if cmds:
    OPERATOR_IN.write_text("\n".join(cmds) + "\n", encoding="utf-8")
    log("soft_fix: " + ", ".join(cmds))
  return cmds


def hard_restart() -> None:
  log("HARD RESTART (40m kiegyensúlyozott)")
  save_state(pending_restart=False, pending_restart_reasons=[], pending_restart_since="")
  subprocess.run(["bash", str(START_SCRIPT)], check=False, timeout=120)
  time.sleep(8)
  log("post-restart: start_overnight_40m.sh lefutott")


def assess() -> tuple[str, list[str]]:
  """Return (verdict, reasons). verdict: ok | soft | restart"""
  reasons: list[str] = []
  st = load_status()
  prev = load_state()

  if not gui_running():
    return "restart", ["run_ft8_gui.py nem fut"]

  if not st:
    return "restart", ["gui_status.json hiányzik vagy sérült"]

  age = status_age_sec(st)
  if age is not None and age > STALE_STATUS_SEC:
    reasons.append(f"gui_status elavult ({age:.0f}s)")

  if st.get("safety_tripped"):
    reasons.append(f"safety_tripped: {st.get('safety_reason', '?')}")

  if st.get("last_tx_error"):
    reasons.append(f"last_tx_error: {st.get('last_tx_error')}")

  if st.get("tx_active"):
    tx_age = age if age is not None else 0
    if tx_age > STUCK_TX_SEC:
      reasons.append(f"tx_active túl sokáig ({tx_age:.0f}s)")

  if reasons:
    return "restart", reasons

  decode = int(st.get("decode_count") or 0)
  prev_decode = prev.get("decode_count")
  flat_count = int(prev.get("flat_decode_checks") or 0)
  tx_age = last_tx_age_sec()

  if prev_decode is not None and decode <= prev_decode:
    if tx_age is None or tx_age > 1800:
      flat_count += 1
    else:
      flat_count = 0
  else:
    flat_count = 0

  save_state(
    decode_count=decode,
    flat_decode_checks=flat_count,
    qso_phase=st.get("qso_phase"),
    qso_partner=st.get("qso_partner"),
    cq_only_mode=st.get("cq_only_mode"),
  )

  if flat_count >= FLAT_DECODE_CHECKS:
    return "restart", [f"nincs új dekód {flat_count} ellenőrzés óta (~{flat_count * 30} perc)"]

  soft_needed = (
    not st.get("rx_running")
    or not st.get("pro_operator")
    or not st.get("ptt_armed")
    or str(st.get("band", "")) != EXPECTED_BAND
    or abs(float(st.get("dial_mhz") or 0) - EXPECTED_DIAL) > DIAL_TOL
    or bool(st.get("cq_only_mode")) != EXPECTED_CQ_ONLY
    or str(st.get("pro_priority", "")).lower() != EXPECTED_PRO_PRIORITY
    or bool(st.get("map_visible", True)) != EXPECTED_MAP
    or int(st.get("cq_wait_periods") or 0) != EXPECTED_CQ_WAIT
  )
  if soft_needed:
    return "soft", ["RX/PRO/PTT/sáv/mód javítás szükséges"]

  if not bridge_running():
    return "soft", ["ft8_live_bridge.py nem fut — auto_watch kezeli"]

  return "ok", []


def main() -> int:
  st = load_status()
  state = load_state()

  # Korábban halasztott restart — QSO vége után most
  if state.get("pending_restart") and not qso_in_progress(st):
    reasons = state.get("pending_restart_reasons") or ["halasztott restart"]
    log("halasztott RESTART — QSO véget ért: " + "; ".join(reasons))
    hard_restart()
    time.sleep(5)
    v2, r2 = assess()
    if v2 == "ok":
      log("RESTART sikeres")
      return 0
    log("RESTART után még gond: " + "; ".join(r2))
    return 1

  verdict, reasons = assess()

  summary = {
    "verdict": verdict,
    "reasons": reasons,
    "phase": st.get("qso_phase"),
    "partner": st.get("qso_partner"),
    "decode_count": st.get("decode_count"),
    "band": st.get("band"),
    "dial_mhz": st.get("dial_mhz"),
    "rx": st.get("rx_running"),
    "ptt": st.get("ptt_armed"),
    "pro": st.get("pro_operator"),
    "cq_only": st.get("cq_only_mode"),
    "pro_priority": st.get("pro_priority"),
    "safety_tripped": st.get("safety_tripped"),
    "qso_busy": qso_in_progress(st),
    "pending_restart": bool(state.get("pending_restart")),
  }
  log("check: " + json.dumps(summary, ensure_ascii=False))

  if verdict == "ok":
    if state.get("pending_restart") and qso_in_progress(st):
      log(
        f"QSO folyamatban ({st.get('qso_partner')}, {st.get('qso_phase')}) — "
        f"halasztott restart vár"
      )
    else:
      log("OK — nincs beavatkozás")
    return 0

  if verdict == "soft":
    soft_fix(st)
    log("SOFT FIX — várunk")
    return 0

  if qso_in_progress(st) and not is_critical_restart(reasons):
    if pending_restart_expired(state):
      log(
        f"RESTART — QSO halasztás lejárt ({PENDING_RESTART_MAX_SEC}s): "
        f"{st.get('qso_partner')} ({st.get('qso_phase')})"
      )
    else:
      save_state(
        pending_restart=True,
        pending_restart_reasons=reasons,
        pending_restart_since=state.get("pending_restart_since") or time_iso_utc(time.time()),
      )
      log(
        f"RESTART halasztva — QSO folyamatban: {st.get('qso_partner')} "
        f"({st.get('qso_phase')}); ok: {reasons}"
      )
      return 0

  hard_restart()
  time.sleep(5)
  v2, r2 = assess()
  if v2 == "ok":
    log("RESTART sikeres")
    return 0
  log("RESTART után még gond: " + "; ".join(r2))
  return 1


if __name__ == "__main__":
  sys.exit(main())
