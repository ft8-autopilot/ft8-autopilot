#!/usr/bin/env python3
"""FT8 protokoll / etikett szabálykeresés a gyűjtött decode logokból."""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from cw_discover.ft8.decode_meta import classify_message_type
from cw_discover.paths import LOG_DIR
OUT_JSON = ROOT / "data" / "ft8_protocol_rules.json"
OUT_MD = ROOT / "data" / "FT8_PROTOCOL_ANALYSIS.md"

CQ_RE = re.compile(r"^CQ(\s+DX|\s+[A-Z0-9/]+)?", re.I)
REPORT_RE = re.compile(r"^(R{1,3}|R[+-]?\d{1,2}|73|RR73|[+-]?\d{1,2})$", re.I)
GRID4_RE = re.compile(r"^[A-R]{2}[0-9]{2}$", re.I)


def _parse_cq_variant(message: str) -> str:
  parts = message.strip().split()
  if not parts or parts[0].upper() != "CQ":
    return "CQ"
  if len(parts) == 1:
    return "CQ"
  p1 = parts[1].upper()
  if p1 == "DX":
    return "CQ DX"
  if is_callsign(p1):
    return "CQ [call]"
  if GRID4_RE.match(p1):
    return "CQ [grid]"
  return "CQ other"


def _directed_pair(message: str, msg_type: str) -> tuple[str, str] | None:
  calls = extract_callsigns_from_message(message)
  if len(calls) < 2:
    return None
  if msg_type == "cq":
    return None
  return calls[0], calls[1]


def _report_token(message: str) -> str | None:
  for p in message.strip().split():
    if REPORT_RE.match(p):
      return p.upper()
  return None


@dataclass
class CallRun:
  caller: str
  target: str
  cycles: list[str] = field(default_factory=list)
  audio_hz: list[float] = field(default_factory=list)
  snr: list[int] = field(default_factory=list)
  reports: list[str] = field(default_factory=list)
  ended_73: bool = False


def load_decodes() -> list[dict[str, Any]]:
  rows: list[dict[str, Any]] = []
  for fp in sorted(LOG_DIR.glob("*/decodes.jsonl")):
    if fp.parent.name == "1970-01-01":
      continue
    with fp.open(encoding="utf-8") as f:
      for line in f:
        line = line.strip()
        if not line:
          continue
        try:
          rec = json.loads(line)
        except json.JSONDecodeError:
          continue
        rows.append(rec)
  rows.sort(key=lambda r: r.get("time_received", 0))
  return rows


def build_call_grid_cache(rows: list[dict]) -> dict[str, dict]:
  """Hívójel → lokátor (üzenetből + geo cache)."""
  cache: dict[str, dict] = {}
  for rec in rows:
    msg = rec.get("message", "")
    g = rec.get("geo") or {}
    grid_msg = extract_grid_from_message(msg)
    calls = extract_callsigns_from_message(msg)
    ts = rec.get("time_iso", "")
    for call in calls:
      ent = cache.setdefault(call, {"grids": Counter(), "sources": Counter(), "last_seen": ts, "count": 0})
      ent["count"] += 1
      ent["last_seen"] = ts
      if grid_msg:
        ent["grids"][grid_msg] += 1
        ent["sources"]["message"] += 1
      elif g.get("grid"):
        ent["grids"][g["grid"]] += 1
        ent["sources"][g.get("grid_source", "geo")] += 1
  out = {}
  for call, ent in cache.items():
    if not ent["grids"]:
      continue
    grid, n = ent["grids"].most_common(1)[0]
    out[call] = {
      "grid": grid,
      "confidence": round(n / ent["count"], 3),
      "observations": ent["count"],
      "alt_grids": dict(ent["grids"].most_common(3)),
      "last_seen": ent["last_seen"],
    }
  return out


def _cluster_runs(events: list[dict], gap_s: float = 45.0) -> list[list[dict]]:
  if not events:
    return []
  events = sorted(events, key=lambda e: e["ts"])
  clusters: list[list[dict]] = [[events[0]]]
  for ev in events[1:]:
    if ev["ts"] - clusters[-1][-1]["ts"] > gap_s:
      clusters.append([ev])
    else:
      clusters[-1].append(ev)
  return clusters


def analyze(rows: list[dict]) -> dict[str, Any]:
  n = len(rows)
  msg_types = Counter()
  cq_variants = Counter()
  report_tokens = Counter()
  cq_with_grid = 0
  cq_total = 0
  cq_repeat_same_cycle = 0
  cq_by_call: dict[str, list[float]] = defaultdict(list)

  # audio_hz per directed pair caller
  pair_hz: dict[tuple[str, str], list[float]] = defaultdict(list)
  pair_cycles: dict[tuple[str, str], list[str]] = defaultdict(list)

  pair_events: dict[tuple[str, str], list[dict]] = defaultdict(list)
  answer_distances: list[float] = []
  answer_snr: list[int] = []
  completed_threads = 0
  thread_starts = 0

  for rec in rows:
    msg = rec.get("message", "")
    mt = rec.get("msg_type") or classify_message_type(msg)
    msg_types[mt] += 1
    cycle = rec.get("cycle", "")
    ts = rec.get("time_received", 0)
    audio = rec.get("audio_hz")
    snr = rec.get("snr")
    g = rec.get("geo") or {}
    dist = g.get("distance_km")

    if mt == "cq":
      cq_total += 1
      cq_variants[_parse_cq_variant(msg)] += 1
      if extract_grid_from_message(msg):
        cq_with_grid += 1
      caller = extract_callsigns_from_message(msg)
      if caller:
        cq_by_call[caller[0]].append(ts)

    rt = _report_token(msg)
    if rt:
      report_tokens[rt] += 1

    pair = _directed_pair(msg, mt)
    if pair:
      caller, target = pair
      key = (caller, target)
      if audio is not None:
        pair_hz[key].append(float(audio))
      pair_cycles[key].append(cycle)
      pair_events[key].append(
        {
          "ts": float(ts),
          "cycle": cycle,
          "mt": mt,
          "audio": float(audio) if audio is not None else None,
          "snr": int(snr) if snr is not None else None,
          "report": rt,
        }
      )
      if mt in ("qso", "report") and dist:
        answer_distances.append(float(dist))
        if snr is not None:
          answer_snr.append(int(snr))

  runs: list[CallRun] = []
  multi_call_same_target: Counter = Counter()
  for (caller, target), events in pair_events.items():
    clusters = _cluster_runs(events)
    if len(clusters) >= 2:
      multi_call_same_target[len(clusters)] += 1
    for cluster in clusters:
      run = CallRun(caller=caller, target=target)
      for ev in cluster:
        run.cycles.append(ev["cycle"])
        if ev["audio"] is not None:
          run.audio_hz.append(ev["audio"])
        if ev["snr"] is not None:
          run.snr.append(ev["snr"])
        if ev["report"]:
          run.reports.append(ev["report"])
        if ev["mt"] == "73" or ev["report"] in ("73", "RR73"):
          run.ended_73 = True
      runs.append(run)
      if run.ended_73:
        completed_threads += 1
      if any(ev["mt"] in ("qso", "report") for ev in cluster):
        thread_starts += 1

  # CQ repeat intervals (same station)
  cq_intervals = []
  for call, times in cq_by_call.items():
    times = sorted(times)
    for a, b in zip(times, times[1:]):
      dt = b - a
      if 10 < dt < 120:
        cq_intervals.append(dt)

  # persistence stats
  run_lens = [len(set(r.cycles)) for r in runs if len(r.cycles) > 0]
  run_lens_73 = [len(set(r.cycles)) for r in runs if r.ended_73]
  give_up = [len(set(r.cycles)) for r in runs if not r.ended_73 and len(r.cycles) >= 2]

  def pct(xs: list[float], p: float) -> float | None:
    if not xs:
      return None
    xs = sorted(xs)
    i = int(len(xs) * p / 100)
    return xs[min(i, len(xs) - 1)]

  # Hz stability per pair
  hz_spread: list[float] = []
  hz_same_pct: list[float] = []
  for key, hz_list in pair_hz.items():
    if len(hz_list) < 2:
      continue
    spread = max(hz_list) - min(hz_list)
    hz_spread.append(spread)
    rounded = [round(h / 6.25) * 6.25 for h in hz_list]  # FT8 tone ~6.25 Hz
    mode = Counter(rounded).most_common(1)[0][1]
    hz_same_pct.append(100 * mode / len(hz_list))

  # Decision tree rules (empirical thresholds)
  rules = {
    "cq": {
      "include_grid_pct": round(100 * cq_with_grid / cq_total, 1) if cq_total else 0,
      "dominant_variants": cq_variants.most_common(6),
      "typical_repeat_interval_s": {
        "p50": pct(cq_intervals, 50),
        "p75": pct(cq_intervals, 75),
        "p90": pct(cq_intervals, 90),
      },
      "note": "CQ üzenetek ~15 mp-enként ismétlődnek; grid a logban domináns.",
    },
    "calling_persistence": {
      "total_runs": len(runs),
      "median_cycles_per_run": statistics.median(run_lens) if run_lens else None,
      "p75_cycles": pct([float(x) for x in run_lens], 75),
      "p90_cycles": pct([float(x) for x in run_lens], 90),
      "median_cycles_until_73": statistics.median(run_lens_73) if run_lens_73 else None,
      "p75_cycles_until_73": pct([float(x) for x in run_lens_73], 75),
      "give_up_without_73_median": statistics.median(give_up) if give_up else None,
      "give_up_without_73_p75": pct([float(x) for x in give_up], 75),
      "pairs_retried_later": dict(multi_call_same_target.most_common(8)),
      "interpretation": "1 ciklus=15s; sikeres QSO ~3-5 ciklus; sikertelen ~2-3 után feladás.",
    },
    "frequency_strategy": {
      "pair_hz_spread_median": statistics.median(hz_spread) if hz_spread else None,
      "pair_hz_spread_p90": pct(hz_spread, 90),
      "same_tone_bucket_pct_median": statistics.median(hz_same_pct) if hz_same_pct else None,
      "interpretation": "Ugyanazon célra hívás: többnyire ugyanazon audio_hz-en; nagy ugrás ritka.",
    },
    "reports": {
      "top_tokens": report_tokens.most_common(15),
      "r_prefix_pct": round(
        100 * sum(v for k, v in report_tokens.items() if str(k).startswith("R")) / max(1, sum(report_tokens.values())),
        1,
      ),
    },
    "distance_priority": {
      "answer_dist_km_median": statistics.median(answer_distances) if answer_distances else None,
      "answer_dist_km_p25": pct(answer_distances, 25),
      "answer_dist_km_p75": pct(answer_distances, 75),
      "answer_snr_median": statistics.median(answer_snr) if answer_snr else None,
      "answer_snr_p25": pct([float(x) for x in answer_snr], 25) if answer_snr else None,
      "note": "Válaszok távolsága: EU rövid/közepes dominancia; DX ritkább.",
    },
    "qso_completion_rate": {
      "threads_started": thread_starts,
      "threads_with_73": completed_threads,
      "completion_pct": round(100 * completed_threads / max(1, thread_starts), 1),
    },
  }

  # Standard FT8 exchange template from observed sequences
  exchange_templates = Counter()
  for r in runs:
    if r.ended_73 and r.reports:
      exchange_templates[tuple(r.reports[:4])] += 1

  decision_tree = [
    {
      "id": "listen_cq",
      "if": "üzenet CQ",
      "then": [
        "grid kinyerés / call→grid cache",
        "távolság + SNR rangsor",
        "ha SNR>-15 és távolság cél szerint: válasz ugyanazon audio_hz-en",
      ],
    },
    {
      "id": "first_response",
      "if": "CQ hallva, válasz indul",
      "then": [
        f"tipikus ismétlés {rules['cq']['typical_repeat_interval_s'].get('p50', 15)}s",
        "formátum: [saját_call] [cq_call] [saját_grid]",
        f"grid CQ-ban {rules['cq']['include_grid_pct']}% esetben benne van",
      ],
    },
    {
      "id": "report_exchange",
      "if": "qso/report üzenet",
      "then": [
        "SNR jelentés: -24..+9 tipikus",
        "R prefix = már egyszer kapta (R-05, R+01)",
        f"R token arány: {rules['reports']['r_prefix_pct']}%",
      ],
    },
    {
      "id": "persistence",
      "if": "nincs válasz",
      "then": [
        f"ismételd max ~{int(rules['calling_persistence'].get('p75_cycles_until_73') or 5)} ciklust sikeres QSO-ig",
        f"addig ugyanazon Hz-en maradj (median spread {rules['frequency_strategy'].get('pair_hz_spread_median', 0):.0f} Hz)",
        f"ha nincs RR73/73 ~{int(rules['calling_persistence'].get('give_up_without_73_p75') or 3)} ciklus után hagyd (p75)",
      ],
    },
    {
      "id": "close",
      "if": "RR73 vagy 73",
      "then": ["QSO zárva", "ne hívd tovább", "ADIF/JSONL log"],
    },
  ]

  return {
    "meta": {
      "generated_at": datetime.now(tz=timezone.utc).isoformat(),
      "decode_count": n,
      "log_days": sorted({r.get("time_iso", "")[:10] for r in rows if r.get("time_iso")}),
      "home_qth": "Example City FN31",
      "band": "40m",
      "dial_mhz": 7.074,
    },
    "summary": {
      "msg_types": dict(msg_types),
      "cq_total": cq_total,
      "unique_calls_with_grid": len(build_call_grid_cache(rows)),
      "directed_pair_observations": len(pair_hz),
    },
    "empirical_rules": rules,
    "decision_tree_draft": decision_tree,
    "exchange_templates_top": [
      {"reports": list(k), "count": v} for k, v in exchange_templates.most_common(12)
    ],
    "call_grid_cache_stats": {
      "entries": len(build_call_grid_cache(rows)),
      "high_confidence": sum(1 for v in build_call_grid_cache(rows).values() if v["confidence"] >= 0.5),
    },
    "future_own_qso_log": {
      "primary_format": "ADIF 3.1 (.adi) + JSONL mirror",
      "jsonl_fields": [
        "qso_id",
        "time_iso",
        "call",
        "grid",
        "grid_source",
        "mode",
        "band",
        "freq_hz",
        "rst_sent",
        "rst_rcvd",
        "tx_audio_hz",
        "distance_km",
        "azimuth_deg",
        "comment",
        "adif_blob",
      ],
      "import_targets": ["WSJT-X", "N1MM", "Log4OM", "QRZ", "LoTW", "ClubLog"],
      "existing_export": "cw_discover.ft8.session_log.SessionLog.export_adif()",
      "call_grid_lookup": "data/call_grid_cache.json (generated)",
    },
  }


def write_call_grid_cache(rows: list[dict], path: Path) -> None:
  cache = build_call_grid_cache(rows)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(data: dict[str, Any], path: Path) -> None:
  r = data["empirical_rules"]
  lines = [
    "# FT8 protokoll elemzés — cw-discover log",
    "",
    f"Generálva: {data['meta']['generated_at']}",
    f"Dekódok: **{data['meta']['decode_count']}**",
    "",
    "## Összefoglaló",
    "",
    f"- Üzenettípusok: {data['summary']['msg_types']}",
    f"- CQ üzenetek: {data['summary']['cq_total']}",
    f"- Hívójel→lokátor cache: {data['call_grid_cache_stats']['entries']} állomás",
    "",
    "## CQ szokások",
    "",
    f"- Grid a CQ-ban: **{r['cq']['include_grid_pct']}%**",
    f"- Domináns formák: {r['cq']['dominant_variants']}",
    f"- CQ ismétlés p50: **{r['cq']['typical_repeat_interval_s'].get('p50')}s**",
    "",
    "## Hívás kitartás (empirikus)",
    "",
    f"- QSO kísérletek: **{r['calling_persistence']['total_runs']}**",
    f"- Median ciklus/futam: **{r['calling_persistence']['median_cycles_per_run']}** (1 ciklus ≈ 15 s)",
    f"- Median ciklus RR73-ig: **{r['calling_persistence']['median_cycles_until_73']}** (p75: {r['calling_persistence'].get('p75_cycles_until_73')})",
    f"- Feladás 73 nélkül (p75): **{r['calling_persistence']['give_up_without_73_p75']}** ciklus",
    f"- Későbbi újrahívások: {r['calling_persistence'].get('pairs_retried_later')}",
    "",
    "## Frekvencia (audio_hz)",
    "",
    f"- Pár-hívás Hz spread median: **{r['frequency_strategy']['pair_hz_spread_median']}** Hz",
    f"- Ugyanazon tone bucket median: **{r['frequency_strategy']['same_tone_bucket_pct_median']}%**",
  ]
  lines += ["", "## Döntési fa (vázlat)", ""]
  for node in data["decision_tree_draft"]:
    lines.append(f"### {node['id']}")
    lines.append(f"- **Ha:** {node['if']}")
    for step in node["then"]:
      lines.append(f"  - {step}")
    lines.append("")
  lines += [
    "## Saját QSO log (jövő)",
    "",
    f"- Formátum: **{data['future_own_qso_log']['primary_format']}**",
    f"- Mezők: `{', '.join(data['future_own_qso_log']['jsonl_fields'])}`",
    f"- Import: {', '.join(data['future_own_qso_log']['import_targets'])}",
    "",
  ]
  path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
  print("Decode logok betöltése…")
  rows = load_decodes()
  print(f"  {len(rows)} dekód")
  data = analyze(rows)
  write_call_grid_cache(rows, ROOT / "data" / "call_grid_cache.json")
  OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
  OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
  write_markdown(data, OUT_MD)
  print(f"JSON: {OUT_JSON}")
  print(f"MD:   {OUT_MD}")
  print(f"Cache: {ROOT / 'data' / 'call_grid_cache.json'}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
