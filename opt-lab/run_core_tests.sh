#!/usr/bin/env bash
# Gyors opt/CI tesztcsomag (~20s) — chaos/integration nélkül
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"
PY="$ROOT/.venv/bin/pytest"
"$PY" tests/test_ft8_slot.py tests/test_ft8_protocol.py tests/test_qso_controller.py \
  tests/test_ft8_behavior_50.py tests/test_ft8_log_scenarios.py tests/test_opt_perf.py \
  tests/test_slot_native.py tests/test_virtual_engine.py tests/test_ft8_pro.py \
  tests/test_tx_log_repair.py tests/test_decode_inject.py tests/test_operator_decisions.py \
  tests/test_esp_link_guard.py tests/test_gui_controllers.py tests/test_supervision_status.py \
  -q -m "not integration" "$@"
