"""Dekód injektálás parancs tesztek."""
from __future__ import annotations

from cw_discover.ft8.decode_inject import parse_inject_decode_command


def test_parse_inject_decode_basic() -> None:
  r = parse_inject_decode_command("INJECT_DECODE N0CALL DK7ZT JO30")
  assert r is not None
  assert r.message == "N0CALL DK7ZT JO30"
  assert r.snr == -10
  assert r.audio_hz == 1867


def test_parse_inject_decode_with_snr_hz() -> None:
  r = parse_inject_decode_command("INJECT_DECODE N0CALL DK7ZT JO30 -8 1200")
  assert r is not None
  assert r.snr == -8
  assert r.audio_hz == 1200
