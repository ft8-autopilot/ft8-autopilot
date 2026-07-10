# C++ / shared memory — következő fázis (terv)

## 1. FT8 slot timer (`cw_discover/native/slot_timer.cpp`)

- `seconds_until_tx_period(want, t)` — monoton óra, nincs GIL
- Python ctypes binding: `cw_discover/ft8/_slot_native.py`
- Becsült nyereség: **alacsony** (már Python-ban adaptív sleep) — de zero-GIL wait

## 2. Decode ring buffer (shared memory)

```
PyFT8 decode thread → shm_ring_push(json_line)
                              ↓
GUI thread          ← shm_ring_pop_batch(64)
Bridge thread       ← tail offset only (mmap file)
```

- `multiprocessing.shared_memory` vagy `/dev/shm/cw_discover_decodes`
- Bridge ne olvassa újra a teljes fájlt — csak offset + mmap
- Becsült nyereség: **közepes** — kevesebb JSON parse duplikáció

## 3. PyFT8 / LDPC

- Már C-ben fut; GPU offload csak ha batch decode kell (nem élő 15s ciklus)
- **Nem prioritás** élő üzemhez

## Merge checklist (prod ← opt)

```bash
diff -ru cw-discover/cw_discover/ft8/ft8_slot.py ft8-autopilot/cw_discover/ft8/ft8_slot.py
diff -ru cw-discover/cw_discover/ft8/atomic_io.py ft8-autopilot/cw_discover/ft8/atomic_io.py
diff -ru cw-discover/cw_discover/ft8/session_log.py ft8-autopilot/cw_discover/ft8/session_log.py
diff -ru cw-discover/cw_discover/ft8/virtual_engine.py ft8-autopilot/cw_discover/ft8/virtual_engine.py
diff -ru cw-discover/cw_discover/gui/ft8_window.py ft8-autopilot/cw_discover/gui/ft8_window.py
diff -ru cw-discover/scripts/ft8_live_bridge.py ft8-autopilot/scripts/ft8_live_bridge.py
```

**Éles merge előtt:** állítsd le az FT8-et, másold át, futtasd pytest, indítsd újra.
