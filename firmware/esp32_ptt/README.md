# ESP32 PTT firmware

## Verziók

| Fájl | Mikor | Tartalom |
|------|-------|----------|
| `main_2026-07-04_no_watchdog.cpp` | 2026-07-04 11:41 feltöltés | PING, TIME, PTT, AT, STATUS — **nincs** watchdog |
| `main.cpp` | **aktuális / jövőálló** | fenti + SHUTDOWN, RESUME, LOCK, **20 s hardver watchdog** |

A jövőálló verzió **visszafelé kompatibilis**: a Python `Esp32Ptt` minden régi parancsot ismer.

## Tegnapi sikeres feltöltés (2026-07-04 ~11:41)

```bash
cd ~/ai/esp232_kepek/test_d26_optocoupler
# GUI / híd le, breadboard vezetékek le
./scripts/flash_hold_boot.sh
```

**BOOT nyomva tartása** az egész feltöltés alatt → ENTER → várj ~1–2 perc.

Működő kombináció: **esptool `default_reset` @ 115200** + BOOT tartva → `Hash of data verified` → `SIKER`

## Mai jövőálló feltöltés (watchdog)

Ugyanaz a parancs — a `src/main.cpp` már a watchdog verzió:

```bash
cd ~/ai/esp232_kepek/test_d26_optocoupler
chmod +x scripts/*.sh
./scripts/free_serial_port.sh    # GUI le, port szabad
./scripts/flash_hold_boot.sh     # BOOT tartva → ENTER
```

Siker után:

```bash
.venv-pio/bin/python scripts/ptt_host.py ping
# → PONG, FT8_PTT gpio=26 ready
# STATUS → LOCK=0
```

Ha LOCK=1: `RESUME` vagy GUI → Biztonság → Összes újraaktiválás.
