#!/bin/bash
set -e
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -r requirements-ft8.txt
echo "Kész. FT8 GUI: .venv/bin/python scripts/run_ft8_gui.py"
echo "Teljes stack: ./start"
