# FT8 Autopilot

**Open-source FT8 receive/decode, automated QSO handling, and optional auto-CQ/PTT for amateur radio.**

FT8 Autopilot is a Python application that decodes FT8 on the air (via [PyFT8](https://pypi.org/project/PyFT8/)), logs stations, shows a live GUI map, and can run an **automated operator**: CQ calling, answering stations, PRO-style candidate scoring, ADIF/QSO logging, and ESP32 PTT control.

> **Amateur radio license required.** You are responsible for compliant operation on authorized bands and power limits. This software does not replace operator judgment.

## Features

| Area | What it does |
|------|----------------|
| **RX / decode** | PyFT8 decoder, JSONL session logs, live bridge for monitoring |
| **GUI** | PyQt5 main window, world map, decode table, QSO status |
| **Auto operator** | State machine: CQ → grid → report → RR73 → 73, incoming-call priority |
| **PRO mode** | Score CQ candidates (distance, SNR, weak-DX bias), defer pick per cycle |
| **Dupe control** | WSJT-X style: skip **call + band + mode** worked today (UTC day) |
| **Logging** | JSONL QSO log, ADIF export (`upload.adi`), Hungarian tab log optional |
| **PTT** | Serial ESP32 line, TX slot timing, safety watchdog |
| **Performance** | LRU caches, batched JSONL I/O, optional native slot timer (`opt-lab/native`) |

## Quick start

### 1. Clone and virtualenv

```bash
git clone https://github.com/Tomsawier92/ft8-autopilot.git
cd ft8-autopilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**System packages** (Debian/Ubuntu example): `python3-pyqt5`, `portaudio19-dev`, PulseAudio or PipeWire for audio.

### 2. Station configuration

```bash
cp forgalminaplo/station.json.example forgalminaplo/station.json
# Edit callsign, grid, PTT port, audio device
```

| Field | Meaning |
|-------|---------|
| `callsign` | Your callsign |
| `grid` | 4-char Maidenhead locator |
| `ptt_port` | Serial device for PTT (e.g. `/dev/ttyUSB0`) or empty for simulate |
| `pro_operator.enabled` | Auto CQ / answer logic |

### 3. Run GUI only

```bash
PYTHONPATH=. .venv/bin/python scripts/run_ft8_gui.py
```

### 4. Full auto mode (GUI + bridge + watch)

```bash
./start
# or: ./scripts/start_auto_ft8.sh
```

Writes live status to `forgalminaplo/live/gui_status.json`. Operator commands: `forgalminaplo/live/operator_in.txt` (`PTT_ON`, `PRO_ON`, `ABORT_QSO`, …).

### 5. Tests

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_ft8_behavior_50.py -q
# or fast core suite:
opt-lab/run_core_tests.sh
```

## Project layout

```
ft8-autopilot/
├── cw_discover/          # Python package (FT8 engine, GUI, logging)
├── scripts/              # Launchers, bridge, stress tools
├── tests/                # Pytest suite (~150+ tests)
├── data/                 # Protocol docs, behavior specs
├── forgalminaplo/        # Your QSO data (not in git) — see station.json.example
├── opt-lab/              # Benchmarks, native slot timer source
├── requirements.txt
├── start                 # Launcher: start | stop | status | restart
├── README.md             # This file (English)
└── README.hu.md          # Hungarian documentation
```

Runtime directories created on first run: `logs/`, `forgalminaplo/live/`, `state/`.

## Hardware notes

- **Audio**: line-in from radio, PulseAudio/PipeWire sink for TX audio
- **PTT**: ESP32 firmware expected on serial (OK/ERR responses)
- **Simulate TX**: leave `ptt_port` empty or use test harness without GUI PTT

## Documentation

- `data/FT8_BEHAVIOR_50.md` — 50-point behavior specification
- `data/FT8_TEST_GAPS.md` — test coverage notes
- `data/FT8_PRO_OPERATOR_META.md` — PRO scoring design
- `README.hu.md` — full Hungarian guide

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built around **PyFT8**, inspired by WSJT-X / WSJT-Z auto-operating practices and community FT8 automation essays.
