"""operator_in.txt fájl olvasó — mtime alapú változáskövetés."""
from __future__ import annotations

from pathlib import Path


class OperatorInReader:
  def __init__(self, path: Path) -> None:
    self._path = path
    self._mtime = -1.0

  def consume_if_changed(self) -> str | None:
    """Új tartalom esetén visszaadja a szöveget és üríti a fájlt."""
    try:
      st = self._path.stat()
    except OSError:
      return None

    if st.st_size == 0:
      self._mtime = st.st_mtime
      return None
    if st.st_mtime == self._mtime:
      return None

    self._mtime = st.st_mtime
    try:
      text = self._path.read_text(encoding="utf-8").strip()
    except OSError:
      return None
    if not text:
      return None

    try:
      self._path.write_text("", encoding="utf-8")
    except OSError:
      return None
    self._mtime = -1.0
    return text
