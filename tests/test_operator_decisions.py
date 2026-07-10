"""Operátor döntés napló rotáció."""
from __future__ import annotations

import json

from cw_discover.ft8 import operator_decisions as od


def test_decision_log_rotation(tmp_path, monkeypatch) -> None:
  log = tmp_path / "operator_decisions.jsonl"
  monkeypatch.setattr(od, "_DECISION_LOG", log)
  monkeypatch.setattr(od, "_MAX_DECISION_LOG_BYTES", 200)
  big = "x" * 300
  log.write_text(json.dumps({"event": "test", "pad": big}) + "\n", encoding="utf-8")
  od.log_operator_decision("after_rotate", ok=True)
  archives = list(tmp_path.glob("operator_decisions_*.jsonl"))
  assert len(archives) == 1
  assert log.is_file()
  lines = log.read_text(encoding="utf-8").strip().splitlines()
  assert len(lines) == 1
  assert json.loads(lines[0])["event"] == "after_rotate"
