#!/usr/bin/env python3
"""Remove station-specific strings before GitHub publish."""
from __future__ import annotations

import re
import sys
from pathlib import Path

HOME_QTH = '''"""Default home QTH — override via station.json / map GUI."""
from __future__ import annotations

from dataclasses import dataclass

from PyFT8.databases import grid_to_latlong

# Example defaults (replace with your QTH in station.json)
HOME_GRID = "FN31"
HOME_NAME = "Example QTH"
HOME_COUNTRY = "United States"
HOME_LAT = 42.0
HOME_LON = -72.0


@dataclass(frozen=True)
class HomeQth:
  name: str
  country: str
  grid: str
  lat: float
  lon: float

  @classmethod
  def default(cls) -> HomeQth:
    glat, glon = grid_to_latlong(HOME_GRID)
    return cls(
      name=HOME_NAME,
      country=HOME_COUNTRY,
      grid=HOME_GRID,
      lat=HOME_LAT,
      lon=HOME_LON,
    )


DEFAULT_HOME = HomeQth.default()
'''

REPLACEMENTS = [
  ("HA3GX", "N0CALL"),
  ("Siófok", "Example City"),
  ("Balatonszabadi", "Example City"),
  ("horvath.tamas920109@gmail.com", ""),
  ('ME = "N0CALL"', 'ME = "N0CALL"'),  # idempotent after HA3GX→N0CALL
  ("HA3GX jel:", "Station signal:"),
  ("HA3GX self-spill:", "Self-spill:"),
  ('"home_qth": "Example City JN96"', '"home_qth": "Example City FN31"'),
  ("Siófok JN96", "Example City FN31"),
  ("QTH Siófok", "QTH home"),
  ("Saját állomás (Magyarország, Example City) a térképen", "Saját állomás a térképen"),
  ("Távolság Example Citytól", "Távolság QTH-tól"),
  ("siofok()", "default()"),
  ("HomeQth.siofok", "HomeQth.default"),
  ("HA3GX állomás tapasztalat", "operátori tapasztalat"),
]

TEXT_SUFFIXES = {".py", ".md", ".sh", ".txt", ".json", ".example", ".c", ".h"}

SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}


def sanitize_file(path: Path) -> bool:
  try:
    text = path.read_text(encoding="utf-8")
  except (UnicodeDecodeError, OSError):
    return False
  orig = text
  for old, new in REPLACEMENTS:
    text = text.replace(old, new)
  text = re.sub(r'Csak saját CQ \(CQ N0CALL JN96\)', "Csak saját CQ + rád hívók", text)
  if text != orig:
    path.write_text(text, encoding="utf-8")
    return True
  return False


def main() -> int:
  root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
  (root / "cw_discover/ft8/home_qth.py").write_text(HOME_QTH, encoding="utf-8")

  changed = 1
  for path in root.rglob("*"):
    if not path.is_file():
      continue
    if any(part in SKIP_PARTS for part in path.parts):
      continue
    if path.suffix not in TEXT_SUFFIXES and path.name not in ("VERSION",):
      continue
    if path.name == "sanitize_public_tree.py":
      continue
    if sanitize_file(path):
      changed += 1
  print(f"sanitize: {changed} files touched (incl. home_qth.py)")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
