"""Mai naplófájlok keresése — dekódok, QSO, magyar napló."""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cw_discover.ft8.session_log import LOG_DIR
from cw_discover.ft8.station_identity import FORGALMI_DIR

CET = ZoneInfo("Europe/Budapest")


@dataclass(frozen=True)
class LogSearchHit:
  source: str
  time_text: str
  summary: str
  detail: str


def today_log_day() -> str:
  return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def today_naplo_day() -> str:
  return datetime.now(tz=CET).strftime("%Y-%m-%d")


def _parse_iso_utc(iso: str) -> datetime | None:
  if not iso:
    return None
  try:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
  except ValueError:
    return None
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(timezone.utc)


def format_time_cet(iso: str) -> str:
  """Dekód / QSO idő — helyi (Budapest), mint a forgalmi napló."""
  dt = _parse_iso_utc(iso)
  if dt is None:
    return iso[:19].replace("T", " ")
  return dt.astimezone(CET).strftime("%H:%M:%S CET")


def _cycle_slot_label(o: dict) -> str:
  """FT8 15 mp-es slot kezdete — nem a dekód pontos ideje."""
  cst = str(o.get("cycle_start_utc", ""))
  dt = _parse_iso_utc(cst)
  if dt is not None:
    return f"slot {dt.astimezone(CET).strftime('%H:%M:%S')} CET"
  cycle = str(o.get("cycle", ""))
  if "_" in cycle:
    _, hm = cycle.rsplit("_", 1)
    if len(hm) == 6 and hm.isdigit():
      return f"slot {hm[0:2]}:{hm[2:4]}:{hm[4:6]} UTC"
  return ""


def _detail_decode(o: dict) -> str:
  parts: list[str] = []
  band = o.get("band")
  dial = o.get("dial_mhz")
  if band:
    parts.append(str(band))
  if dial is not None:
    parts.append(f"{float(dial):.3f} MHz")
  slot = _cycle_slot_label(o)
  if slot:
    parts.append(slot)
  hz = o.get("audio_hz")
  if hz is not None:
    parts.append(f"{int(hz)} Hz")
  geo = o.get("geo")
  if isinstance(geo, dict):
    grid = geo.get("grid")
    if grid:
      parts.append(str(grid))
  return " · ".join(parts) if parts else str(o.get("message", ""))[:80]


def _detail_qso(o: dict) -> str:
  parts: list[str] = []
  call = str(o.get("call", ""))
  grid = str(o.get("grid", ""))
  if call:
    parts.append(call)
  if grid:
    parts.append(grid)
  band = o.get("band")
  freq = o.get("freq_mhz")
  if band:
    parts.append(str(band))
  if freq is not None:
    parts.append(f"{float(freq):.3f} MHz")
  rs = o.get("rst_sent", "")
  rr = o.get("rst_rcvd", "")
  if rs or rr:
    parts.append(f"RST {rs}/{rr}")
  return " · ".join(parts)


def source_label(source: str) -> str:
  return {
    "decodes": "dekód",
    "candidates": "kandidát",
    "qso": "QSO",
    "naplo": "napló",
  }.get(source, source)


def query_match(text: str, query: str) -> bool:
  """Kis/nagybetű érzéketlen keresés; `?` és `*` wildcard (fnmatch)."""
  q = (query or "").strip()
  if not q:
    return False
  hay = text.lower()
  pat = q.lower()
  if "?" in pat or "*" in pat:
    return fnmatch.fnmatchcase(hay, pat)
  return pat in hay


def _line_matches(line: str, query: str) -> bool:
  if query_match(line, query):
    return True
  try:
    o = json.loads(line)
  except json.JSONDecodeError:
    return False
  msg = str(o.get("message", ""))
  if query_match(msg, query):
    return True
  call = str(o.get("call", ""))
  return bool(call) and query_match(call, query)


def search_today_logs(
  query: str,
  *,
  log_dir: Path | None = None,
  forgalmi_dir: Path | None = None,
  day: str | None = None,
  limit: int = 400,
) -> list[LogSearchHit]:
  """Keresés a mai logfájlokban — részszó vagy wildcard (? *)."""
  q = (query or "").strip()
  if not q:
    return []
  day_key = day or today_log_day()
  hits: list[LogSearchHit] = []
  root = log_dir or LOG_DIR
  forg = forgalmi_dir or FORGALMI_DIR

  def add(source: str, time_text: str, summary: str, detail: str) -> bool:
    if len(hits) >= limit:
      return False
    hits.append(LogSearchHit(source=source, time_text=time_text, summary=summary, detail=detail))
    return True

  dec_path = root / day_key / "decodes.jsonl"
  if dec_path.exists():
    with dec_path.open(encoding="utf-8", errors="replace") as fh:
      for line in fh:
        if not line.strip() or not _line_matches(line, q):
          continue
        try:
          o = json.loads(line)
        except json.JSONDecodeError:
          if not add("decodes", "", line[:120], line.strip()):
            return hits
          continue
        t = format_time_cet(str(o.get("time_iso", "")))
        msg = str(o.get("message", ""))
        snr = o.get("snr")
        summary = f"SNR{int(snr):+d} {msg}" if snr is not None else msg
        if not add("decodes", t, summary, _detail_decode(o)):
          return hits

  cand_path = root / day_key / "candidates.jsonl"
  if cand_path.exists() and len(hits) < limit:
    with cand_path.open(encoding="utf-8", errors="replace") as fh:
      for line in fh:
        if not line.strip() or not _line_matches(line, q):
          continue
        try:
          o = json.loads(line)
        except json.JSONDecodeError:
          continue
        t = format_time_cet(str(o.get("time_iso", "")))
        msg = str(o.get("message", ""))
        if not add("candidates", t, msg, _detail_decode(o)):
          return hits

  qso_path = forg / "qso.jsonl"
  if qso_path.exists() and len(hits) < limit:
    with qso_path.open(encoding="utf-8", errors="replace") as fh:
      for line in fh:
        if not line.strip() or not _line_matches(line, q):
          continue
        try:
          o = json.loads(line)
        except json.JSONDecodeError:
          continue
        t_on = str(o.get("time_on_iso", ""))[:10]
        if t_on != day_key:
          continue
        call = str(o.get("call", ""))
        t = format_time_cet(str(o.get("time_on_iso", "")))
        summary = f"{call} RST {o.get('rst_sent','')}/{o.get('rst_rcvd','')} {o.get('band','')}"
        if not add("qso", t, summary, _detail_qso(o)):
          return hits

  naplo_path = forg / "naplo.txt"
  if naplo_path.exists() and len(hits) < limit:
    with naplo_path.open(encoding="utf-8", errors="replace") as fh:
      for line in fh:
        if not line.strip() or not query_match(line, q):
          continue
        if line.startswith("Dátum") or line.startswith("\t\tCET"):
          continue
        parts = line.split("\t")
        date = parts[0].strip() if parts else ""
        if date != today_naplo_day():
          continue
        t = parts[2].strip() if len(parts) > 2 else ""
        if t and not t.endswith("CET"):
          t = f"{t} CET"
        call = parts[4].strip() if len(parts) > 4 else ""
        detail = line.strip()
        if not add("naplo", t, call or line[:60], detail):
          return hits

  return hits
