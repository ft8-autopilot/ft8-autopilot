"""GUI hibanapló — utolsó 100 bejegyzés, körpuffer + fájl."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cw_discover.ft8.decode_meta import time_iso_utc
from cw_discover.gui.error_catalog import CATALOG, ErrorSpec, classify_tx_error

MAX_ENTRIES = 100
_DEDUP_SECONDS = 12.0

_Sink = Callable[[str, str, str, str, bool], None]
_sink: _Sink | None = None
_journal: "ErrorJournal | None" = None


@dataclass
class ErrorEntry:
  time_utc: str
  category: str
  title: str
  detail: str
  hint: str
  code: str = ""

  def format_block(self) -> str:
    lines = [
      f"{self._local_time()}  [{self.category}]",
      f"  Mi történt: {self.title}",
    ]
    if self.detail:
      lines.append(f"  Részlet: {self.detail}")
    if self.hint:
      lines.append(f"  Teendő: {self.hint}")
    return "\n".join(lines)

  def _local_time(self) -> str:
    try:
      from datetime import datetime, timezone

      dt = datetime.fromisoformat(self.time_utc.replace("Z", "+00:00"))
      if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
      return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
      return self.time_utc[:19].replace("T", " ")


def bind_error_journal(journal: "ErrorJournal") -> None:
  global _journal, _sink
  _journal = journal
  _sink = journal._append_from_report


def report_error(code: str, detail: str = "", *, dedup: bool = True) -> ErrorEntry | None:
  spec = CATALOG.get(code)
  if spec is None:
    return report_raw("Általános", f"Ismeretlen hibakód: {code}", detail=detail, dedup=dedup)
  if _journal is None:
    return None
  return _journal.append_spec(spec, detail=detail, dedup=dedup)


def report_raw(
  category: str,
  title: str,
  *,
  detail: str = "",
  hint: str = "",
  dedup: bool = True,
) -> ErrorEntry | None:
  if _journal is None:
    return None
  return _journal.append(category, title, detail=detail, hint=hint, dedup=dedup)


def report_tx_error(message: str, error: str) -> ErrorEntry | None:
  code = classify_tx_error(error)
  if code is not None:
    spec = CATALOG[code]
    detail = message
    if error and error not in spec.title:
      detail = f"{message} — {error}" if message else error
    if _journal is None:
      return None
    return _journal.append_spec(spec, detail=detail, dedup=True)
  return report_raw(
    "TX / PTT",
    error or "Ismeretlen TX hiba",
    detail=message,
    hint="Nézd a Hibanaplót és a forgalminaplo/live/tx.log fájlt",
  )


class ErrorJournal:
  """Utolsó MAX_ENTRIES hiba — legrégebbi esik ki."""

  def __init__(self, path: Path | None = None) -> None:
    self._path = path
    self._entries: list[ErrorEntry] = []
    self._last_dedup_key = ""
    self._last_dedup_mono = 0.0
    if path is not None:
      self.load(path)

  @property
  def count(self) -> int:
    return len(self._entries)

  def entries_newest_first(self) -> list[ErrorEntry]:
    return list(reversed(self._entries))

  def codes_recorded(self) -> set[str]:
    return {e.code for e in self._entries if e.code}

  def _append_from_report(
    self,
    category: str,
    title: str,
    detail: str,
    hint: str,
    dedup: bool,
  ) -> None:
    self.append(category, title, detail=detail, hint=hint, dedup=dedup)

  def append_spec(self, spec: ErrorSpec, *, detail: str = "", dedup: bool = True) -> ErrorEntry | None:
    return self.append(
      spec.category,
      spec.title,
      detail=detail,
      hint=spec.hint,
      dedup=dedup,
      code=spec.code,
    )

  def append(
    self,
    category: str,
    title: str,
    *,
    detail: str = "",
    hint: str = "",
    dedup: bool = True,
    code: str = "",
  ) -> ErrorEntry | None:
    key = f"{code or category}|{title}|{detail}"
    now = time.monotonic()
    if dedup and key == self._last_dedup_key and (now - self._last_dedup_mono) < _DEDUP_SECONDS:
      return None
    entry = ErrorEntry(
      time_utc=time_iso_utc(time.time()),
      category=category.strip() or "Általános",
      title=title.strip(),
      detail=detail.strip(),
      hint=hint.strip(),
      code=code,
    )
    if not entry.title:
      return None
    self._entries.append(entry)
    if len(self._entries) > MAX_ENTRIES:
      self._entries = self._entries[-MAX_ENTRIES:]
    self._last_dedup_key = key
    self._last_dedup_mono = now
    self.save()
    return entry

  def log_tx_error(self, message: str, error: str) -> ErrorEntry | None:
    return report_tx_error(message, error)

  def clear(self) -> None:
    self._entries.clear()
    self._last_dedup_key = ""
    self._last_dedup_mono = 0.0
    self.save()

  def save(self, path: Path | None = None) -> None:
    p = path or self._path
    if p is None:
      return
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [asdict(e) for e in self._entries]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

  def load(self, path: Path | None = None) -> None:
    p = path or self._path
    if p is None or not p.exists():
      return
    try:
      raw = json.loads(p.read_text(encoding="utf-8"))
      rows = raw.get("entries") if isinstance(raw, dict) else raw
      if not isinstance(rows, list):
        return
      loaded: list[ErrorEntry] = []
      for row in rows[-MAX_ENTRIES:]:
        if not isinstance(row, dict):
          continue
        loaded.append(
          ErrorEntry(
            time_utc=str(row.get("time_utc", "")),
            category=str(row.get("category", "Általános")),
            title=str(row.get("title", "")),
            detail=str(row.get("detail", "")),
            hint=str(row.get("hint", "")),
            code=str(row.get("code", "")),
          )
        )
      self._entries = [e for e in loaded if e.title]
    except (OSError, json.JSONDecodeError, TypeError):
      self._entries = []
