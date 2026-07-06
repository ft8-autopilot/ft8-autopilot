"""GUI kiegészítők — worked QSO, log keresés, cooldown."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from cw_discover.ft8.forgalmi_log import ForgalmiNaplo
from cw_discover.ft8.log_search import query_match, search_today_logs
from cw_discover.ft8.qso_controller import Ft8AutoOperator
from cw_discover.ft8.station_identity import StationIdentity
from cw_discover.ft8.tx_player import Ft8TxPlayer


def test_worked_calls_today(tmp_path: Path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  naplo = ForgalmiNaplo(tmp_path, station=st)
  day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
  (tmp_path / "qso.jsonl").write_text(
    json.dumps(
      {
        "call": "OE7JUM",
        "band": "40m",
        "mode": "FT8",
        "time_on_iso": f"{day}T10:00:00+00:00",
      }
    )
    + "\n"
    + json.dumps(
      {
        "call": "PC2J",
        "band": "20m",
        "mode": "FT8",
        "time_on_iso": f"{day}T12:00:00+00:00",
      }
    )
    + "\n",
    encoding="utf-8",
  )
  naplo._load_worked_cache()
  assert naplo.worked_calls_today() == {"OE7JUM", "PC2J"}
  assert naplo.worked_calls_today(band="20m") == {"PC2J"}


def test_search_today_logs_qso(tmp_path: Path, monkeypatch) -> None:
  from cw_discover.ft8 import log_search as ls

  day = "2026-07-05"
  log_dir = tmp_path / "logs"
  forg = tmp_path / "forgalmi"
  forg.mkdir()
  (log_dir / day).mkdir(parents=True)
  (forg / "qso.jsonl").write_text(
    json.dumps(
      {
        "call": "DG9SEH",
        "band": "20m",
        "rst_sent": "-04",
        "rst_rcvd": "+00",
        "time_on_iso": f"{day}T13:19:00+00:00",
      }
    )
    + "\n",
    encoding="utf-8",
  )
  monkeypatch.setattr(ls, "today_log_day", lambda: day)
  hits = search_today_logs("DG9SEH", log_dir=log_dir, forgalmi_dir=forg, day=day)
  assert any(h.source == "qso" and "DG9SEH" in h.summary for h in hits)


def test_query_match_wildcards() -> None:
  assert query_match("DG9SEH", "dg*")
  assert query_match("DG9SEH", "dg?seh")
  assert query_match("CQ N0CALL JN96", "cq * jn96")
  assert not query_match("DG9SEH", "ha*")
  assert query_match("R-13", "r-??")


def test_search_today_logs_wildcard(tmp_path: Path, monkeypatch) -> None:
  from cw_discover.ft8 import log_search as ls

  day = "2026-07-05"
  log_dir = tmp_path / "logs"
  forg = tmp_path / "forgalmi"
  forg.mkdir()
  (log_dir / day).mkdir(parents=True)
  (forg / "qso.jsonl").write_text(
    json.dumps(
      {
        "call": "DG9SEH",
        "band": "20m",
        "rst_sent": "-04",
        "rst_rcvd": "+00",
        "time_on_iso": f"{day}T13:19:00+00:00",
      }
    )
    + "\n"
    + json.dumps(
      {
        "call": "PC2J",
        "band": "20m",
        "rst_sent": "-10",
        "rst_rcvd": "-05",
        "time_on_iso": f"{day}T14:00:00+00:00",
      }
    )
    + "\n",
    encoding="utf-8",
  )
  monkeypatch.setattr(ls, "today_log_day", lambda: day)
  hits = search_today_logs("DG*", log_dir=log_dir, forgalmi_dir=forg, day=day)
  assert len(hits) == 1
  assert hits[0].source == "qso" and "DG9SEH" in hits[0].summary


def test_search_today_logs_decode_time_and_detail(tmp_path: Path, monkeypatch) -> None:
  from cw_discover.ft8 import log_search as ls

  day = "2026-07-05"
  log_dir = tmp_path / "logs"
  forg = tmp_path / "forgalmi"
  forg.mkdir()
  (log_dir / day).mkdir(parents=True)
  (log_dir / day / "decodes.jsonl").write_text(
    json.dumps(
      {
        "message": "N0CALL EH3WWA -07",
        "snr": 7,
        "band": "40m",
        "dial_mhz": 7.074,
        "audio_hz": 497,
        "cycle": "260705_000245",
        "cycle_start_utc": "2026-07-05T00:02:45+00:00",
        "time_iso": "2026-07-05T00:02:57.118582+00:00",
        "geo": {"grid": "JN11", "grid_source": "cache"},
      }
    )
    + "\n",
    encoding="utf-8",
  )
  monkeypatch.setattr(ls, "today_log_day", lambda: day)
  hits = search_today_logs("N0CALL", log_dir=log_dir, forgalmi_dir=forg, day=day)
  assert len(hits) == 1
  h = hits[0]
  assert h.time_text == "02:02:57 CET"
  assert "slot 02:02:45 CET" in h.detail
  assert "40m" in h.detail and "497 Hz" in h.detail
  assert "JN11" in h.detail
  assert "{" not in h.detail


def test_outbound_cooldown_calls(tmp_path: Path) -> None:
  st = StationIdentity(callsign="N0CALL", grid="JN96", ptt_port="")
  op = Ft8AutoOperator(station=st, naplo=ForgalmiNaplo(tmp_path, station=st), tx=Ft8TxPlayer(simulate=True))
  op._mark_outbound_failed("SP2OSA")
  assert "SP2OSA" in op.outbound_cooldown_calls()
  assert op._is_outbound_cooldown("SP2OSA")
