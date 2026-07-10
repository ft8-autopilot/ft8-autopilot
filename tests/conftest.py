"""Hypothesis profilok — property-based fuzz mélység."""
from __future__ import annotations

from hypothesis import Phase, HealthCheck, settings

# Alap: gyors CI (~1e3 példa / teszt)
settings.register_profile(
  "default",
  max_examples=1000,
  deadline=None,
  suppress_health_check=[HealthCheck.too_slow],
)

# Mély: ~1e4
settings.register_profile(
  "thorough",
  max_examples=10_000,
  deadline=None,
  suppress_health_check=[HealthCheck.too_slow],
)

# Stressz: ~1e5 — „milliókhoz közel” több teszt × profil
settings.register_profile(
  "stress",
  max_examples=100_000,
  deadline=None,
  phases=[Phase.generate, Phase.target, Phase.shrink],
  suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

settings.load_profile("default")


def pytest_configure(config):
  import os
  from pathlib import Path

  # Tesztek ne írjanak az élő operator_decisions.jsonl-be.
  if not os.environ.get("FT8_OPERATOR_DECISIONS"):
    test_log = Path(__file__).resolve().parent / ".operator_decisions_test.jsonl"
    os.environ["FT8_OPERATOR_DECISIONS"] = str(test_log)
    # Új modul-import előtt állítjuk — reload szükséges ha már importálva volt.
    import cw_discover.ft8.operator_decisions as od

    od._DECISION_LOG = test_log

  config.addinivalue_line("markers", "hypothesis_stress: nagy max_examples fuzz (lassú)")
  config.addinivalue_line("markers", "integration: élő folyamat / I/O integrációs teszt")
