#!/usr/bin/env python3
"""Hot path benchmark — ft8-autopilot."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cw_discover.ft8.atomic_io import DailyBufferedWriter
from cw_discover.ft8.ft8_slot import seconds_until_tx_period, wait_for_tx_period
from cw_discover.ft8.session_log import SessionLog


def bench_slot_wait() -> dict:
  """Slot belépés pontossága (következő páros periódus)."""
  from cw_discover.ft8.ft8_slot import ft8_period_at

  want = ft8_period_at(time.time() + 5)
  t0 = time.perf_counter()
  # max 5s várakozás teszt
  deadline = time.time() + 5.0
  while time.time() < deadline:
    d = seconds_until_tx_period(want)
    if d <= 0:
      break
    time.sleep(min(d, 0.05))
  elapsed = time.perf_counter() - t0
  err_ms = abs(seconds_until_tx_period(want)) * 1000
  return {"slot_wait_s": round(elapsed, 3), "boundary_err_ms": round(err_ms, 1)}


def bench_jsonl_flush(n: int = 2000) -> dict:
  with tempfile.TemporaryDirectory() as td:
    w = DailyBufferedWriter(Path(td))
    t0 = time.perf_counter()
    base = time.time()
    for i in range(n):
      w.append_decode(
        {
          "time_received": base + i * 0.01,
          "message": f"CQ TEST{i % 50}",
          "snr": -10,
          "id": i,
        }
      )
    w.flush()
    elapsed = time.perf_counter() - t0
  return {"decodes": n, "flush_s": round(elapsed, 3), "per_decode_us": round(elapsed / n * 1e6, 1)}


def bench_session_add(n: int = 500) -> dict:
  slog = SessionLog()
  slog.reset("40m", 7.074)
  t0 = time.perf_counter()
  for i in range(n):
    slog.add_decode(
      decode_id=i,
      message=f"CQ DX TEST{i % 30} JN96",
      snr=-12 + (i % 5),
      rf_khz=7074.0,
      cycle="260705_120000",
      audio_hz=1500,
      dt=0.0,
      time_received=time.time(),
    )
  elapsed = time.perf_counter() - t0
  return {"decodes": n, "add_s": round(elapsed, 3), "per_decode_us": round(elapsed / n * 1e6, 1)}


def bench_tail_read(n_chunks: int = 500, chunk_size: int = 20) -> dict:
  import tempfile
  from cw_discover.ft8.decode_tail import MmapJsonlTail
  from cw_discover.ft8.json_fast import dumps_line

  with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "decodes.jsonl"
    tail = MmapJsonlTail(p)
    t0 = time.perf_counter()
    total = 0
    for i in range(n_chunks):
      with p.open("ab") as f:
        for j in range(chunk_size):
          f.write(dumps_line({"id": i * chunk_size + j, "message": f"CQ T{j}"}))
      total += len(tail.read_new())
    elapsed = time.perf_counter() - t0
  return {"records": total, "tail_s": round(elapsed, 3), "per_chunk_us": round(elapsed / n_chunks * 1e6, 1)}


def bench_triplet_cache(n: int = 10000) -> dict:
  from cw_discover.ft8.ft8_protocol import message_triplet

  msgs = [f"CQ DX TEST{i % 200} JN96" for i in range(200)]
  t0 = time.perf_counter()
  for i in range(n):
    message_triplet(msgs[i % len(msgs)])
  return {"calls": n, "triplet_s": round(time.perf_counter() - t0, 4)}


def bench_preamble_cache(n: int = 10000) -> dict:
  from cw_discover.ft8.decode_meta import message_preamble

  msgs = [f"CQ HA1ABC JN96" if i % 2 == 0 else f"HA1ABC N0CALL -12" for i in range(200)]
  t0 = time.perf_counter()
  for i in range(n):
    message_preamble(msgs[i % len(msgs)])
  return {"calls": n, "preamble_s": round(time.perf_counter() - t0, 4)}


def main() -> int:
  results = {
    "slot": bench_slot_wait(),
    "jsonl_flush": bench_jsonl_flush(),
    "session_add": bench_session_add(),
    "tail_read": bench_tail_read(),
    "triplet_cache": bench_triplet_cache(),
    "preamble_cache": bench_preamble_cache(),
  }
  out = Path(__file__).parent / "bench_results.json"
  out.write_text(json.dumps(results, indent=2) + "\n")
  for k, v in results.items():
    print(f"{k}: {v}")
  print(f"→ {out}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
