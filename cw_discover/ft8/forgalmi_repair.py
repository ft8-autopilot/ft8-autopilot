"""Forgalmi napló javítás — RST adott tx.log-ból, teszt QSO-k eltávolítása."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cw_discover.ft8.forgalmi_log import CET, QsoRecord, record_to_adif_line
from cw_discover.ft8.ft8_protocol import (
  is_73,
  is_report,
  is_rr73,
  is_rrr,
  message_triplet,
  rst_from_report_token,
)
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.paths import TX_LOG

TX_START_RE = re.compile(
  r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?)\s+TX_START\s+(?P<msg>.+?)\s+\|"
)
TEST_CALL_RE = re.compile(r"^LOOP\d+$", re.IGNORECASE)


def is_test_call(call: str) -> bool:
  return bool(TEST_CALL_RE.match((call or "").strip().upper()))


def our_report_sent(message: str, remote: str, me: str) -> str | None:
  triplet = message_triplet(message)
  if triplet is None or triplet.call_a != remote.upper() or triplet.call_b != me.upper():
    return None
  third = triplet.third
  if not is_report(third) or is_73(third) or is_rr73(third) or is_rrr(third):
    return None
  return rst_from_report_token(third)


def load_tx_starts(path: Path | None = None) -> list[tuple[datetime, str]]:
  tx_path = path or TX_LOG
  if not tx_path.exists():
    return []
  out: list[tuple[datetime, str]] = []
  for line in tx_path.read_text(encoding="utf-8").splitlines():
    m = TX_START_RE.match(line.strip())
    if not m:
      continue
    ts_raw = m.group("ts").replace("Z", "+00:00")
    if "+" not in ts_raw and not ts_raw.endswith("Z"):
      ts_raw += "+00:00"
    ts = datetime.fromisoformat(ts_raw)
    if ts.tzinfo is None:
      ts = ts.replace(tzinfo=timezone.utc)
    out.append((ts, m.group("msg").strip()))
  return out


def rst_sent_from_tx_log(
  *,
  call: str,
  time_on: datetime,
  time_off: datetime,
  tx_starts: list[tuple[datetime, str]],
  me: str,
  margin_s: float = 45.0,
) -> str | None:
  remote = call.strip().upper()
  t0 = time_on.astimezone(timezone.utc) - timedelta(seconds=margin_s)
  t1 = time_off.astimezone(timezone.utc) + timedelta(seconds=margin_s)
  reports: list[str] = []
  for ts, msg in tx_starts:
    if ts < t0 or ts > t1:
      continue
    rst = our_report_sent(msg, remote, me)
    if rst:
      reports.append(rst)
  return reports[-1] if reports else None


def repair_qso_records(
  records: list[dict],
  *,
  tx_starts: list[tuple[datetime, str]] | None = None,
  station: StationIdentity | None = None,
  drop_test_calls: bool = True,
) -> tuple[list[dict], list[str]]:
  """Javítja az RST adott mezőket; opcionálisan kiszűri a teszt hívójeleket."""
  st = station or StationIdentity.load()
  me = st.callsign.upper()
  tx = tx_starts if tx_starts is not None else load_tx_starts()
  notes: list[str] = []
  repaired: list[dict] = []

  for rec in records:
    call = str(rec.get("call", "")).strip().upper()
    if drop_test_calls and is_test_call(call):
      notes.append(f"eltávolítva teszt QSO: {call} ({rec.get('qso_id', '')[:8]})")
      continue

    rst_sent = str(rec.get("rst_sent", "")).strip()
    if rst_sent in ("", "+00"):
      try:
        t_on = datetime.fromisoformat(str(rec["time_on_iso"]))
        t_off = datetime.fromisoformat(str(rec["time_off_iso"]))
      except (KeyError, ValueError, TypeError):
        repaired.append(rec)
        continue
      fixed = rst_sent_from_tx_log(
        call=call, time_on=t_on, time_off=t_off, tx_starts=tx, me=me
      )
      if fixed and fixed != rst_sent:
        rec = dict(rec)
        rec["rst_sent"] = fixed
        blob = rec.get("adif_blob")
        if isinstance(blob, dict):
          blob = dict(blob)
          blob["rst_sent"] = fixed
          rec["adif_blob"] = blob
        notes.append(f"{call}: rst_sent {rst_sent or '(üres)'} → {fixed}")
    repaired.append(rec)
  return repaired, notes


def qso_dict_to_record(rec: dict) -> QsoRecord:
  t_on = datetime.fromisoformat(str(rec["time_on_iso"]))
  t_off = datetime.fromisoformat(str(rec["time_off_iso"]))
  if t_on.tzinfo is None:
    t_on = t_on.replace(tzinfo=timezone.utc)
  if t_off.tzinfo is None:
    t_off = t_off.replace(tzinfo=timezone.utc)
  return QsoRecord(
    call=str(rec["call"]),
    grid=str(rec.get("grid", "")),
    grid_source=str(rec.get("grid_source", "message")),
    band=str(rec.get("band", "")),
    dial_mhz=float(rec.get("freq_mhz", rec.get("dial_mhz", 0.0))),
    mode=str(rec.get("mode", "FT8")),
    rst_sent=str(rec.get("rst_sent", "")),
    rst_rcvd=str(rec.get("rst_rcvd", "")),
    time_on=t_on,
    time_off=t_off,
    tx_audio_hz=int(rec.get("tx_audio_hz", 0)),
    distance_km=rec.get("distance_km"),
    azimuth_deg=rec.get("azimuth_deg"),
    comment=str(rec.get("comment", "")),
    partner_name=str(rec.get("partner_name", "")),
    partner_qth=str(rec.get("partner_qth", "")),
  )


def rebuild_export_files(
  records: list[dict],
  root: Path,
  *,
  station: StationIdentity | None = None,
) -> None:
  """naplo.txt és upload.adi újraépítése a qso rekordokból."""
  st = station or StationIdentity.load()
  root.mkdir(parents=True, exist_ok=True)

  hungarian_header = (
    "Dátum\t\tKezdés\tVégzés\tHívójel\tFrek.\tÜzemmód\tRST\tRST\tTelj.\tNév\tQTH\n"
    "\t\tCET\tCET\t\tMhz\t\tAdott\tVett\tWatt\t\t\n"
  )
  hungarian_lines: list[str] = []
  adif_lines: list[str] = []
  for rec in records:
    q = qso_dict_to_record(rec)
    t_on = q.time_on.astimezone(CET)
    t_off = q.time_off.astimezone(CET)
    hungarian_lines.append(
      f"{t_on.strftime('%Y-%m-%d')}\t\t"
      f"{t_on.strftime('%H:%M')}\t{t_off.strftime('%H:%M')}\t"
      f"{q.call}\t{q.dial_mhz:.5f}\t{q.mode}\t"
      f"{q.rst_sent}\t{q.rst_rcvd}\t{st.tx_power_w}\t"
      f"{q.partner_name}\t{q.partner_qth}\n"
    )
    adif_lines.append(record_to_adif_line(q, st) + "\n")

  (root / "naplo.txt").write_text(hungarian_header + "".join(hungarian_lines), encoding="utf-8")
  adif_header = (
    "ADIF Export from cw-discover FT8 QSO\n"
    "<programid:11>cw-discover\n"
    f"<created:15>{datetime.now(tz=timezone.utc).strftime('%d-%b-%Y %H%M')} UTC\n"
    "<eoh>\n"
  )
  (root / "upload.adi").write_text(adif_header + "".join(adif_lines), encoding="utf-8")


def load_qso_jsonl(path: Path) -> list[dict]:
  if not path.exists():
    return []
  out: list[dict] = []
  for line in path.read_text(encoding="utf-8").splitlines():
    if line.strip():
      out.append(json.loads(line))
  return out


def write_qso_jsonl(path: Path, records: list[dict]) -> None:
  text = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records)
  path.write_text(text, encoding="utf-8")
