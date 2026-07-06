#!/usr/bin/env python3
"""Forgalmi napló javítás — RST adott tx.log-ból, teszt QSO-k törlése, export újraépítés."""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cw_discover.ft8.forgalmi_repair import (  # noqa: E402
  load_qso_jsonl,
  load_tx_starts,
  rebuild_export_files,
  repair_qso_records,
  write_qso_jsonl,
)
from cw_discover.ft8.station_identity import FORGALMI_DIR  # noqa: E402


def main() -> int:
  ap = argparse.ArgumentParser(description="Forgalmi napló RST javítás tx.log alapján")
  ap.add_argument("--root", type=Path, default=FORGALMI_DIR, help="forgalminaplo könyvtár")
  ap.add_argument("--tx-log", type=Path, default=None, help="tx.log útvonal (alap: live/tx.log)")
  ap.add_argument("--dry-run", action="store_true", help="csak kiírás, nincs írás")
  args = ap.parse_args()

  root = args.root
  jsonl_path = root / "qso.jsonl"
  records = load_qso_jsonl(jsonl_path)
  if not records:
    print("nincs QSO a qso.jsonl-ben")
    return 1

  tx_starts = load_tx_starts(args.tx_log)
  repaired, notes = repair_qso_records(records, tx_starts=tx_starts)
  print(f"QSO: {len(records)} → {len(repaired)} ({len(records) - len(repaired)} törölve)")
  for note in notes:
    print(f"  • {note}")

  plus00_before = sum(1 for r in records if r.get("rst_sent") == "+00")
  plus00_after = sum(1 for r in repaired if r.get("rst_sent") == "+00")
  print(f"RST adott +00: {plus00_before} → {plus00_after}")

  if args.dry_run:
    return 0

  stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
  backup = root / f"qso.jsonl.bak-{stamp}"
  shutil.copy2(jsonl_path, backup)
  print(f"biztonsági mentés: {backup}")

  write_qso_jsonl(jsonl_path, repaired)
  rebuild_export_files(repaired, root)
  print(f"frissítve: {jsonl_path}, naplo.txt, upload.adi")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
