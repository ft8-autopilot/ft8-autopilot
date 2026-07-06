# FT8 automata operátor — 50 kötelező viselkedés

**Források:** WSJT-X 2.x FT8 üzemmód, PyFT8 `progress_qso`, cw-discover 31k+ dekód
elemzés, KK5JY / WSJT-Z / Auto-FT8 best practice, N0CALL állomás tapasztalat.

**Cél:** A `Ft8AutoOperator` + `Ft8TxPlayer` + GUI viselkedése egyezik a szabványos
FT8 fél-duplex QSO-val.

---

## A. Időzítés és slotok (1–10)

| # | Viselkedés | WSJT-X / FT8 szabály |
|---|-----------|----------------------|
| 1 | Egy FT8 **ciklus = 15,0 s** | Globális UTC slotok :00, :15, :30, :45 |
| 2 | **Fél-duplex:** egy slotban TX *vagy* RX, soha egyszerre | Állomások váltakoznak |
| 3 | **Páros/páratlan periódus:** p0 = :00/:30 UTC, p1 = :15/:45 UTC | 30 s-enként ismétlődik |
| 4 | Saját TX **csak a kijelölt periódusban** (tx_period) | „TX even/odd seconds” |
| 5 | CQ válasz: **ellentétes periódus** a hallott CQ-hoz képest | Ő CQ-zik p1-en → mi p0-on |
| 6 | TX indulás **≤ 2,5 s** a slot eleje után (`MAX_TX_START_SECONDS`) | Késői TX érvénytelen |
| 7 | `wait_for_tx_period()` vár a **következő saját slotra**, nem csak 15 s határra | Pontos slot-illesztés |
| 8 | Újraküldés (retry) **csak saját periódusban**, max ~30 s-enként | Nem minden 15 s-ben |
| 9 | Dekód **max ~17,5 s** korú lehet (`decode_is_fresh`) | PyFT8: ne reagálj régi slotra |
| 10 | NTP szinkron: Δt ≤ 0,5 s ideális a vételhez | Dekóder `dt` mező |

## B. Üzenetformátumok (11–20)

| # | Viselkedés | Formátum |
|---|-----------|----------|
| 11 | Saját CQ: `CQ [saját_call] [grid4]` | pl. `CQ N0CALL JN96` |
| 12 | CQ válasz: `[remote] [saját] [grid4]` | pl. `IK4LZH N0CALL JN96` |
| 13 | Jelentés küldés: `[remote] [saját] [+/-NN]` | pl. `IK4LZH N0CALL -12` |
| 14 | Jelentés vissza: `[remote] [saját] R[+/-NN]` | `R` prefix = már egyszer kapta |
| 15 | Zárás 1: `[remote] [saját] RR73` | Köszönés előtt |
| 16 | Zárás 2: `[remote] [saját] 73` | Végső búcsú |
| 17 | Bejövő üzenet: **call_a = hívó (REMOTE), call_b = mi (DE)** | `IK4LZH N0CALL -09` |
| 18 | **Saját TX visszahallása** (`N0CALL REMOTE …`) → **ignorálás** | Line-in visszacsatolás |
| 19 | Grid token: 4 karakter Maidenhead (pl. `JN96`) | Nem report, nem 73 |
| 20 | Report token: `+/-` és szám (pl. `-09`, `R-05`) | SNR jelzés, nem dB pontos |

## C. QSO állapotgép (21–35)

| # | Viselkedés | Állapot / akció |
|---|-----------|-----------------|
| 21 | Fázisok: `idle` → `calling_cq` → `active` → `closing` → `idle` | |
| 22 | CQ hallva → `active`, első TX: grid küldés | `_answer_cq` |
| 23 | Bejövő hívás (`REMOTE N0CALL GRID`) → `active`, TX: grid | Magasabb prioritás |
| 24 | Remote grid érkezik → TX: SNR report (`+/-NN`) | `rst_sent` mentés |
| 25 | Remote report érkezik → TX: `R+/-NN` | `rst_rcvd` mentés |
| 26 | Remote `R-report` vagy `RRR` → TX: `RR73` | `closing` |
| 27 | Remote `RR73` vagy `73` → TX: `73`, majd **QSO LOG** | `idle` |
| 28 | Nincs válasz **3 saját TX ciklus** után → feladás | `MAX_RETRY_CYCLES = 3` |
| 29 | Feladás után: `idle`, CQ buffer ürítés | Új cél keresése |
| 30 | Aktív QSO alatt **ugyanazon audio_hz**-en maradunk | Tone követés |
| 31 | Új TX üzenet **törli a várakozó TX sort** (nem dupláz) | `_drain_tx_queue` |
| 32 | Retry **nem reseteli** a `cycles_without_reply` számlálót | `is_retry=True` |
| 33 | Új (nem retry) TX **reseteli** a számlálót | Válasz érkezett |
| 34 | `closing` fázisban még egy RR73/73 csere | Rövid zárás |
| 35 | QSO vége: `forgalminaplo` — `naplo.txt` + `upload.adi` + `qso.jsonl` | ADIF kompatibilis |

## D. Operátor prioritás (36–42)

| # | Viselkedés | Sorrend |
|---|-----------|---------|
| 36 | **1.** Aktív QSO folytatása (73-ig) | Legmagasabb |
| 37 | **2.** Bejövő hívás (minket szólítanak) | CQ-zás felett |
| 38 | **3.** CQ válasz (SNR + távolság szűrő) | PRO rangsor |
| 39 | **4.** Saját CQ üresjáratban | `cq_repeat_cycles` |
| 40 | PRO: CQ jelöltek **ciklus végén** egy legjobb kiválasztása | `defer_cq_pick` |
| 41 | PRO: ma már worked állomás **kihagyása** (UTC nap + sáv, WSJT-X dupe) | `skip_worked_today` |
| 42 | PRO: bejövő hívás **felülírhatja** beragadt QSO-t (nincs válasz) | `_should_preempt` |

## E. TX / RX hardver (43–47)

| # | Viselkedés | Megvalósítás |
|---|-----------|--------------|
| 43 | PTT ON **TX slot elején**, OFF a 12,64 s jel után | ESP32 + optocsatoló |
| 44 | PTT OK ellenőrzés (`OK PTT 1/0`) — hiba esetén **nem „sikeres” TX** | `Esp32Ptt` |
| 45 | RX feed **szünetel TX alatt** (fél-duplex) | `set_rx_paused` |
| 46 | TX hang **L+R stereo** (mindkét vonalkimenet) | `_mono_to_stereo` |
| 47 | TX napló: `TX_START` / `TX_OK` / `PTT_FAIL` → `live/tx.log` | Diagnosztika |

## F. Naplózás és megfigyelés (48–50)

| # | Viselkedés | Hely |
|---|-----------|------|
| 48 | Minden dekód → `decodes.jsonl` + live bridge | AI / GUI |
| 49 | GUI státusz: `qso_phase`, `tx_active`, `ptt_armed` | `gui_status.json` |
| 50 | Slot audit: TX csak konzisztens periódusban | `audit_tx_slots.py` |

---

## Tipikus QSO sorozat (referencia)

```
Ciklus  p   IK4LZH (DX)              N0CALL (mi)
──────  ─   ─────────────────────    ─────────────────────
  1    p1   CQ IK4LZH JN54           (vétel)
  2    p0   (vétel)                   IK4LZH N0CALL JN96   ← grid válasz
  3    p1   IK4LZH N0CALL -09          (vétel)
  4    p0   (vétel)                   IK4LZH N0CALL R-12   ← R-jelentés
  5    p1   IK4LZH N0CALL R-08         (vétel)
  6    p0   (vétel)                   IK4LZH N0CALL RR73
  7    p1   IK4LZH N0CALL RR73         (vétel)
  8    p0   (vétel)                   IK4LZH N0CALL 73       ← QSO LOG
```

**Üzenetváltás logika (PyFT8 / WSJT-X):**
- Remote **report** (`-09`) → mi **R-report** (`R-12`)
- Remote **R-report** (`R-08`) → mi **RR73**
- Remote **RR73** → mi **73** + napló

---

## Implementációs státusz (cw-discover)

| Kategória | Pontok | Státusz |
|-----------|--------|---------|
| A Időzítés | 1–10 | ✅ implementálva + tesztelve |
| B Üzenetek | 11–20 | ✅ implementálva + tesztelve |
| C QSO gép | 21–35 | ✅ implementálva, részben integrációs teszt |
| D Prioritás | 36–42 | ✅ implementálva |
| E Hardver | 43–47 | ✅ implementálva (PTT tesztelve) |
| F Napló | 48–50 | ✅ implementálva |

**Automatikus tesztek:**
- `tests/test_ft8_behavior_50.py` — 45 teszt (50 pont spec)
- `tests/test_ft8_log_scenarios.py` — **35 forgatókönyv** (log + anomáliák)
- Összesen **~85 FT8 teszt** (slot, protocol, controller)

**Szimuláció (nincs rádió TX):**
```bash
cd ~/ai/cw-discover
PYTHONPATH=. .venv/bin/python ~/ai/forgalminaplo/scripts/ft8_sim_replay.py \
  -m "CQ IK4LZH JN54" -m "IK4LZH N0CALL -09" -m "IK4LZH N0CALL R-05" -m "IK4LZH N0CALL RR73"
```

| Pont | Teszt | Megjegyzés |
|------|-------|------------|
| 1–10 | `test_a01`…`test_a10` | Időzítés, slot |
| 11–20 | `test_b11`…`test_b20` | Üzenetformátum |
| 21–35 | `test_c21`…`test_c35` | QSO állapotgép |
| 36–42 | `test_d36`…`test_d42` | Prioritás |
| 43 | — | Hardver (kézi PTT teszt) |
| 44–47 | `test_e44`, `test_e46`, `test_e47` | PTT parser, stereo, log |
| 45 | — | RX pause (GUI integráció) |
| 48–50 | `test_f48`…`test_f50` | Napló útvonalak + audit script |
