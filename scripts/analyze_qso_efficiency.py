#!/usr/bin/env python3
"""QSO hatékonyság elemzés — tx.log + decodes.jsonl + auto_watch + qso.jsonl."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cw_discover.ft8.callsign import valid_remote_call
from cw_discover.ft8.ft8_protocol import is_grid_token, is_report, message_triplet
from cw_discover.ft8.ft8_slot import cycle_key_at, period_from_cycle
from cw_discover.paths import FORGALMI_LIVE, LOG_DIR

ME = "N0CALL"
TX_RE = re.compile(
  r"^(\d{4}-\d{2}-\d{2}T[\d:.+Z-]+)\s+TX_START\s+(.+?)\s+\|\s+(\d+)\s+Hz\s+p([01])"
)
ABANDON_RE = re.compile(r"Feladás:\s+(\S+)")
PREEMPT_RE = re.compile(r"PRO váltás →\s+(\S+)")
WATCH_RE = re.compile(
  r"\[(\d{2}:\d{2}:\d{2})\]\s+állapot:\s+phase=(\w+)\s+tx=(\w+)\s+last='([^']*)'\s+note='([^']*)'"
)


@dataclass
class TxEvent:
  ts: datetime
  message: str
  hz: int
  period: int
  target: str
  is_cq: bool
  cycle: str


@dataclass
class Collision:
  tx: TxEvent
  incoming_calls: list[str]
  incoming_snrs: dict[str, int]
  preempt_would_fire: bool
  same_cycle: bool
  next_cycle: bool


@dataclass
class AbandonEvent:
  ts: datetime
  call: str
  later_qso: bool
  later_incoming: bool
  minutes_to_next_qso: float | None


def parse_ts(s: str) -> datetime:
  s = s.replace("Z", "+00:00")
  if s.endswith("+00:00") or "+" in s[10:]:
    return datetime.fromisoformat(s)
  return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def load_tx(path: Path) -> list[TxEvent]:
  out: list[TxEvent] = []
  for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    m = TX_RE.match(line.strip())
    if not m:
      continue
    ts = parse_ts(m.group(1))
    msg = m.group(2).strip()
    tri = message_triplet(msg)
    if tri is None:
      continue
    if tri.is_cq:
      target = "CQ"
      is_cq = True
    else:
      target = tri.call_a if tri.call_b == ME else tri.call_b
      is_cq = False
    if not is_cq and not valid_remote_call(target):
      continue
    out.append(
      TxEvent(
        ts=ts,
        message=msg,
        hz=int(m.group(3)),
        period=int(m.group(4)),
        target=target,
        is_cq=is_cq,
        cycle=cycle_key_at(ts.timestamp()),
      )
    )
  return out


def directed_incoming(message: str, calls: list[str]) -> str | None:
  tri = message_triplet(message)
  if tri is None:
    return None
  if tri.call_b == ME and valid_remote_call(tri.call_a):
    return tri.call_a.upper()
  if tri.call_a == ME and valid_remote_call(tri.call_b):
    third = tri.third
    if is_grid_token(third) or is_report(third):
      return tri.call_b.upper()
  for c in calls:
    cu = c.upper()
    if cu == ME:
      continue
    if valid_remote_call(cu) and ME in message.upper().split():
      parts = message.upper().split()
      if len(parts) >= 2 and parts[0] == cu and parts[1] == ME:
        return cu
      if len(parts) >= 2 and parts[0] == ME and parts[1] == cu:
        if is_grid_token(parts[2]) if len(parts) > 2 else False:
          return cu
  return None


def load_decodes_by_cycle(log_dir: Path, days: list[str]) -> dict[str, list[dict]]:
  by_cycle: dict[str, list[dict]] = defaultdict(list)
  for day in days:
    p = log_dir / day / "decodes.jsonl"
    if not p.exists():
      continue
    with p.open(encoding="utf-8") as f:
      for line in f:
        try:
          r = json.loads(line)
        except json.JSONDecodeError:
          continue
        cyc = r.get("cycle") or ""
        if cyc:
          by_cycle[cyc].append(r)
  return by_cycle


def adjacent_cycles(cycle: str) -> list[str]:
  from cw_discover.ft8.ft8_slot import cycle_start_timestamp

  try:
    t0 = cycle_start_timestamp(cycle)
  except ValueError:
    return [cycle]
  out = []
  for delta in (-15, 0, 15):
    out.append(cycle_key_at(t0 + delta))
  return out


def analyze_collisions(tx_events: list[TxEvent], by_cycle: dict[str, list[dict]]) -> list[Collision]:
  collisions: list[Collision] = []
  outbound = [t for t in tx_events if not t.is_cq]
  # group consecutive TX to same target
  streaks: list[tuple[str, list[TxEvent]]] = []
  cur_call, cur_list = "", []
  for tx in outbound:
    if tx.target != cur_call:
      if cur_list:
        streaks.append((cur_call, cur_list))
      cur_call, cur_list = tx.target, [tx]
    else:
      cur_list.append(tx)
  if cur_list:
    streaks.append((cur_call, cur_list))

  for target, streak in streaks:
    if len(streak) < 2:
      continue
    for tx in streak[1:]:  # retry slots
      inc: dict[str, int] = {}
      same = False
      nxt = False
      for cyc in adjacent_cycles(tx.cycle):
        for r in by_cycle.get(cyc, []):
          inc_call = directed_incoming(r.get("message", ""), r.get("calls") or [])
          if inc_call and inc_call != target:
            inc[inc_call] = int(r.get("snr", 0))
            if cyc == tx.cycle:
              same = True
            elif cyc != tx.cycle:
              nxt = True
      if inc:
        retry_idx = streak.index(tx)
        preempt = retry_idx >= 1  # cycles_without_reply >= 1 after first TX period
        collisions.append(
          Collision(
            tx=tx,
            incoming_calls=sorted(inc.keys()),
            incoming_snrs=inc,
            preempt_would_fire=preempt,
            same_cycle=same,
            next_cycle=nxt,
          )
        )
  return collisions


def load_qsos(path: Path) -> list[dict]:
  if not path.exists():
    return []
  out = []
  for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    out.append(json.loads(line))
  return out


def load_abandons(watch_path: Path) -> list[tuple[datetime, str]]:
  if not watch_path.exists():
    return []
  out = []
  base_date = None
  for line in watch_path.read_text(encoding="utf-8", errors="replace").splitlines():
    m = WATCH_RE.search(line)
    if not m:
      continue
    t_local = m.group(1)
    note = m.group(5)
    am = ABANDON_RE.search(note)
    if not am:
      continue
    # auto_watch has no date — infer from file order using tx dates
    out.append((t_local, am.group(1).rstrip(")")))
  return out


def main() -> int:
  tx_path = FORGALMI_LIVE / "tx.log"
  watch_path = FORGALMI_LIVE / "auto_watch.log"
  qso_path = ROOT / "forgalminaplo" / "qso.jsonl"

  days = sorted(p.name for p in LOG_DIR.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", p.name))
  print(f"=== QSO hatékonyság elemzés ({ME}) ===")
  print(f"Log napok: {days[0]} … {days[-1]} ({len(days)} nap)")
  print()

  tx_events = load_tx(tx_path)
  print(f"TX_START események: {len(tx_events)}")
  outbound = [t for t in tx_events if not t.is_cq]
  cq = [t for t in tx_events if t.is_cq]
  print(f"  outbound: {len(outbound)}, CQ: {len(cq)}")
  print()

  by_cycle = load_decodes_by_cycle(LOG_DIR, days)
  print(f"Dekód ciklusok indexelve: {len(by_cycle):,}")
  print()

  collisions = analyze_collisions(tx_events, by_cycle)
  missed_preempt = [c for c in collisions if c.preempt_would_fire]
  blocked_preempt = [c for c in collisions if not c.preempt_would_fire]
  print("--- Outbound retry közben bejövő hívás ---")
  print(f"Összes retry slot + bejövő: {len(collisions)}")
  print(f"  PRO preempt ELVILEG aktív (≥2. TX ugyanarra): {len(missed_preempt)}")
  print(f"  Preempt NEM aktív (1. retry, még nincs cycles_without_reply≥1): {len(blocked_preempt)}")
  if missed_preempt:
  # count unique incoming during stuck outbound
    inc_counter = Counter()
    for c in missed_preempt:
      for ic in c.incoming_calls:
        inc_counter[ic] += 1
    print(f"  Top bejövő retry közben: {inc_counter.most_common(8)}")
  print()

  # streak analysis
  streak_lens = Counter()
  cur, n = "", 0
  for tx in outbound:
    if tx.target == cur:
      n += 1
    else:
      if cur:
        streak_lens[n] += 1
      cur, n = tx.target, 1
  if cur:
    streak_lens[n] += 1
  print("--- Outbound TX streak hossz (azonos partner) ---")
  for k in sorted(streak_lens):
    print(f"  {k} TX: {streak_lens[k]} eset")
  abandon_streaks = sum(v for k, v in streak_lens.items() if k >= 4)
  print(f"  ≥4 TX (feladás küszöb): {abandon_streaks} eset")
  print()

  qsos = load_qsos(qso_path)
  qso_calls = {q["call"].upper() for q in qsos}
  qso_by_call: dict[str, list[datetime]] = defaultdict(list)
  for q in qsos:
    qso_by_call[q["call"].upper()].append(parse_ts(q["time_on_iso"]))

  # abandons from watch notes with timestamps from tx.log correlation
  abandon_calls: list[tuple[datetime, str]] = []
  for line in watch_path.read_text(encoding="utf-8", errors="replace").splitlines():
    m = WATCH_RE.search(line)
    if not m or "Feladás:" not in m.group(5):
      continue
    am = ABANDON_RE.search(m.group(5))
    if am:
      # use line context — find nearest tx date from file section
      abandon_calls.append((datetime.min.replace(tzinfo=timezone.utc), am.group(1)))

  # better: parse Feladás from full watch with date inference from qso range
  abandon_events: list[AbandonEvent] = []
  abandon_from_tx = []
  last_ts = None
  for tx in outbound:
    last_ts = tx.ts
  # detect abandons via streak end at 4+ without qso
  cur_call, streak = "", []
  for tx in outbound:
    if tx.target != cur_call:
      if len(streak) >= 4:
        abandon_from_tx.append((streak[-1].ts, cur_call))
      cur_call, streak = tx.target, [tx]
    else:
      streak.append(tx)
  if len(streak) >= 4:
    abandon_from_tx.append((streak[-1].ts, cur_call))

  for ts, call in abandon_from_tx:
    later_qso = call.upper() in qso_calls
    later_incoming = False
    mins = None
    times = qso_by_call.get(call.upper(), [])
    for qt in times:
      if qt > ts:
        later_qso = True
        mins = (qt - ts).total_seconds() / 60
        break
    abandon_events.append(
      AbandonEvent(ts, call, later_qso, later_incoming, mins)
    )

  print("--- Outbound feladás (≥4 TX streak) ---")
  print(f"Esetek: {len(abandon_events)}")
  won_later = [a for a in abandon_events if a.later_qso]
  print(f"  Később mégis QSO ugyanazzal: {len(won_later)} ({100*len(won_later)/max(1,len(abandon_events)):.0f}%)")
  if won_later:
    avg_mins = sum(a.minutes_to_next_qso or 0 for a in won_later) / len(won_later)
    print(f"  Átlag idő feladás → későbbi QSO: {avg_mins:.0f} perc")
    print(f"  Példák: {[(a.call, round(a.minutes_to_next_qso or 0)) for a in won_later[:6]]}")
  print()

  # QSO duration / efficiency
  durations = []
  for q in qsos:
    t0 = parse_ts(q["time_on_iso"])
    t1 = parse_ts(q["time_off_iso"])
    durations.append((t1 - t0).total_seconds())
  if durations:
    durations.sort()
    print("--- QSO időtartam (time_on → time_off) ---")
    print(f"  QSO-k: {len(durations)}")
    print(f"  medián: {durations[len(durations)//2]:.0f}s, átlag: {sum(durations)/len(durations):.0f}s")
    print(f"  ≤60s: {sum(1 for d in durations if d<=60)}, ≤90s: {sum(1 for d in durations if d<=90)}, >120s: {sum(1 for d in durations if d>120)}")
  print()

  # incoming QSO vs outbound QSO (first TX direction)
  first_tx_to: dict[str, str] = {}
  for tx in outbound:
    if tx.target not in first_tx_to:
      first_tx_to[tx.target] = tx.message
  incoming_led = 0
  outbound_led = 0
  for q in qsos:
    call = q["call"].upper()
    ft = first_tx_to.get(call, "")
    if not ft:
      incoming_led += 1
    elif call in ft and ft.index(call) < ft.index(ME):
      incoming_led += 1
    else:
      outbound_led += 1
  print("--- QSO irány (első TX alapján) ---")
  print(f"  Bejövő vezette: {incoming_led}")
  print(f"  Outbound/CQ vezette: {outbound_led}")
  print()

  # hour histogram QSO
  hour_ctr = Counter()
  for q in qsos:
    t = parse_ts(q["time_on_iso"])
    hour_ctr[t.hour] += 1
  print("--- QSO óránként (UTC) ---")
  for h in sorted(hour_ctr):
    bar = "█" * (hour_ctr[h] // 3)
    print(f"  {h:02d}: {hour_ctr[h]:3d} {bar}")
  print()

  # preempt notes from watch
  preempt_notes = 0
  for line in watch_path.read_text(encoding="utf-8", errors="replace").splitlines():
    if "PRO váltás" in line:
      preempt_notes += 1
  print(f"--- PRO váltás (watch log) ---")
  print(f"  Események: {preempt_notes}")
  print()

  # CQ wait impact: time between CQ TX
  cq_gaps = []
  for i in range(1, len(cq)):
    gap = (cq[i].ts - cq[i - 1].ts).total_seconds()
    if gap < 600:
      cq_gaps.append(gap)
  if cq_gaps:
    cq_gaps.sort()
    print("--- CQ TX közötti idő ---")
    print(f"  medián: {cq_gaps[len(cq_gaps)//2]:.0f}s ({cq_gaps[len(cq_gaps)//2]/15:.1f} periódus)")
    print(f"  ≤30s: {sum(1 for g in cq_gaps if g<=30)} (back-to-back)")
    print(f"  45-75s: {sum(1 for g in cq_gaps if 45<=g<=75)} (1 periódus wait)")
    print(f"  ≥90s: {sum(1 for g in cq_gaps if g>=90)}")
  print()

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
