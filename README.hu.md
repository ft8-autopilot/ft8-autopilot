# FT8 Autopilot

**Nyílt forráskódú FT8 vevő/dekóder, automata QSO-kezelés és opcionális auto-CQ/PTT amatőr rádiózáshoz.**

Az FT8 Autopilot Python alkalmazás: [PyFT8](https://pypi.org/project/PyFT8/) dekódolás élő sávról, állomásnapló, GUI térkép, és **automata operátor** — CQ hívás, válaszadás, PRO pontozás, ADIF/QSO napló, ESP32 PTT vezérlés.

> **Érvényes amatőr jogosítvány szükséges.** A felelősség az operátoré: szabályos sáv, teljesítmény, és együttműködés. A szoftver nem helyettesíti az operátori döntést.

## Fő funkciók

| Terület | Leírás |
|---------|--------|
| **RX / dekód** | PyFT8, JSONL session log, live bridge |
| **GUI** | PyQt5 ablak, világtérkép, dekód táblázat, QSO státusz |
| **Auto operátor** | Állapotgép: CQ → grid → jelentés → RR73 → 73, bejövő prioritás |
| **PRO mód** | CQ jelöltek pontozása (táv, SNR, gyenge-DX), ciklus végi választás |
| **Dupe** | WSJT-X stílus: **hívójel + sáv + mód** ma (UTC nap) már worked → kihagyás |
| **Napló** | JSONL QSO, ADIF export, opcionális magyar tab |
| **PTT** | ESP32 soros, TX slot időzítés, safety watchdog |
| **Teljesítmény** | LRU cache, batch JSONL, natív slot timer (`opt-lab/native`) |

## Gyors telepítés

### 1. Klón és venv

```bash
git clone https://github.com/ft8-autopilot/ft8-autopilot.git
cd ft8-autopilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Rendszer csomagok** (Debian/Ubuntu): `python3-pyqt5`, `portaudio19-dev`, PulseAudio vagy PipeWire.

### 2. Állomás beállítás

```bash
cp forgalminaplo/station.json.example forgalminaplo/station.json
# Szerkeszd: hívójel, lokátor, PTT port, hang eszköz
```

| Mező | Jelentés |
|------|----------|
| `callsign` | Saját hívójel |
| `grid` | 4 karakteres Maidenhead lokátor |
| `ptt_port` | PTT soros port (pl. `/dev/ttyUSB0`), üres = szimuláció |
| `pro_operator.enabled` | Automata CQ / válasz |

### 3. Csak GUI

```bash
PYTHONPATH=. .venv/bin/python scripts/run_ft8_gui.py
```

### 4. Teljes auto üzem

```bash
./scripts/start_auto_ft8.sh
```

Élő státusz: `forgalminaplo/live/gui_status.json`. Parancsok: `forgalminaplo/live/operator_in.txt` (`PTT_ON`, `PRO_ON`, `ABORT_QSO`, …).

### 5. Tesztek

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_ft8_behavior_50.py -q
opt-lab/run_core_tests.sh
```

## Könyvtárstruktúra

```
ft8-autopilot/
├── cw_discover/          # Python csomag
├── scripts/              # Indítók, bridge, stressz eszközök
├── tests/                # Pytest (~150+ teszt)
├── data/                 # Protokoll és viselkedés dokumentáció
├── forgalminaplo/        # Saját QSO adatok (nincs a gitben)
├── opt-lab/              # Benchmark, natív slot timer
├── requirements.txt
├── README.md             # Angol
└── README.hu.md          # Ez a fájl
```

Első futáskor létrejön: `logs/`, `forgalminaplo/live/`, `state/`.

## Hardver

- **Hang**: rádió line-in, TX-hez PulseAudio/PipeWire kimenet
- **PTT**: ESP32 soros (OK/ERR válaszok)
- **Szimuláció**: üres `ptt_port` vagy teszt harness PTT nélkül

## Dokumentáció

- `data/FT8_BEHAVIOR_50.md` — 50 pontos viselkedés specifikáció
- `data/FT8_TEST_GAPS.md` — teszt lefedettség
- `data/FT8_PRO_OPERATOR_META.md` — PRO pontozás
- `README.md` — English guide

## Licenc

MIT — lásd [LICENSE](LICENSE).

## Köszönet

**PyFT8** alap, WSJT-X / WSJT-Z auto-operátor gyakorlatok és közösségi FT8 esszék ihlette.
