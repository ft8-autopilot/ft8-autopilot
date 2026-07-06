"""Autonóm GUI vezérlő-teszt — pytest wrapper."""
from __future__ import annotations

from scripts.autotest_ft8_gui import run_autotest


def test_ft8_gui_all_controls() -> None:
  report = run_autotest()
  failed = [r for r in report.results if not r.ok]
  assert not failed, "\n".join(f"{r.name}: {r.detail}" for r in failed)
