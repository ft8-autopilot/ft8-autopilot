"""Hibanapló katalógus — életszerű hibák cím + teendő."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
  code: str
  category: str
  title: str
  hint: str


CATALOG: dict[str, ErrorSpec] = {
  # --- ESP32 / PTT / soros (7) ---
  "esp_safety_lock": ErrorSpec(
    "esp_safety_lock",
    "ESP32",
    "Az ESP32 PTT tiltva van (SAFETY_LOCK) — nem lehet adni",
    "Biztonság → ESP32 feloldás (RESUME), vagy Összes újraaktiválás",
  ),
  "esp_ping_fail": ErrorSpec(
    "esp_ping_fail",
    "ESP32",
    "Az ESP32 nem válaszol (PING / soros port)",
    "USB kábel, /dev/ttyUSB0, más program ne foglalja a portot",
  ),
  "esp_ptt_on_fail": ErrorSpec(
    "esp_ptt_on_fail",
    "ESP32",
    "A PTT bekapcsolása sikertelen — az adás nem indult el",
    "Ellenőrizd az ESP32-t, optót, DATA portot; majd ESP32 feloldás",
  ),
  "esp_ptt_off_fail": ErrorSpec(
    "esp_ptt_off_fail",
    "ESP32",
    "A PTT kikapcsolása sikertelen — ragadhat az adás",
    "Azonnal: Biztonság → ESP32 leállítás vagy feloldás; nézd a rádió PTT lámpát",
  ),
  "esp_resume_fail": ErrorSpec(
    "esp_resume_fail",
    "ESP32",
    "ESP32 tiltás feloldása (RESUME) sikertelen",
    "USB újracsatlakoztatás, majd Biztonság → ESP32 feloldás",
  ),
  "esp_shutdown": ErrorSpec(
    "esp_shutdown",
    "ESP32",
    "ESP32 szándékos leállítás (SHUTDOWN) — PTT tiltva",
    "Biztonság → ESP32 feloldás (RESUME)",
  ),
  "esp_ptt_stuck_warn": ErrorSpec(
    "esp_ptt_stuck_warn",
    "ESP32",
    "Az ESP32 ragadó PTT-t észlelt (WARN PTT_STUCK)",
    "Ellenőrizd a rádiót; ha rendben: ESP32 feloldás, majd újra PTT",
  ),
  "esp_usb_serial": ErrorSpec(
    "esp_usb_serial",
    "ESP32",
    "USB / soros kapcsolat megszakadt vagy nem elérhető",
    "CP2102 USB, kábel, port foglaltság; zárd be a másik programot a porton",
  ),
  # --- Biztonság (4) ---
  "safety_trip_stuck_ptt": ErrorSpec(
    "safety_trip_stuck_ptt",
    "Biztonság",
    "Biztonsági leállítás — ragadó PTT (20 s watchdog)",
    "Ellenőrizd a rádiót; Biztonság → ESP32 feloldás vagy Összes újraaktiválás",
  ),
  "safety_startup_tripped": ErrorSpec(
    "safety_startup_tripped",
    "Biztonság",
    "A program biztonsági tiltással indult",
    "Biztonság → ESP32 feloldás vagy Összes újraaktiválás",
  ),
  "safety_reactivate_fail": ErrorSpec(
    "safety_reactivate_fail",
    "Biztonság",
    "Összes újraaktiválás sikertelen",
    "ESP32 feloldás külön; USB; majd újraindítás",
  ),
  "safety_mcu_inactive": ErrorSpec(
    "safety_mcu_inactive",
    "Biztonság",
    "Az ESP32 mikrokontroller nem aktív (SHUTDOWN / LOCK)",
    "Biztonság → ESP32 feloldás (RESUME)",
  ),
  # --- TX / FT8 (4) ---
  "tx_encode_fail": ErrorSpec(
    "tx_encode_fail",
    "TX / FT8",
    "FT8 üzenet kódolása sikertelen",
    "Ellenőrizd a hívójelet, gridet és az üzenet szövegét",
  ),
  "tx_audio_fail": ErrorSpec(
    "tx_audio_fail",
    "TX / Hang",
    "Hangkártya hiba az adás közben",
    "Hangkártya menü; zárd a másik programot a vonal kimenetről",
  ),
  "tx_aborted": ErrorSpec(
    "tx_aborted",
    "TX / Üzem",
    "Az adás megszakítva (Stop / kilépés / vészleállítás)",
    "Normális, ha szándékosan állítottad le",
  ),
  "tx_cancelled": ErrorSpec(
    "tx_cancelled",
    "TX / Üzem",
    "Az adás várakozó sora törölve (új QSO / abort)",
    "Normális üzem — nem feltétlenül hiba",
  ),
  # --- Hang / PulseAudio (4) ---
  "audio_pactl_timeout": ErrorSpec(
    "audio_pactl_timeout",
    "Hang",
    "PulseAudio (pactl) nem válaszol időben — hangrendszer túlterhelt",
    "Várj 10–15 mp; zárd a felesleges programokat; szükség esetén újraindítás",
  ),
  "audio_line_sink_missing": ErrorSpec(
    "audio_line_sink_missing",
    "Hang",
    "A vonalkimenet (rádió audio sink) nem található",
    "Hangkártya menü; pactl list sinks; ellenőrizd az analog-stereo sink nevet",
  ),
  "audio_underrun": ErrorSpec(
    "audio_underrun",
    "Hang",
    "ALSA underrun — az audio stream nem kapott elég adatot",
    "Csökkentsd a terhelést; ne futtass tesztet élő GUI mellett",
  ),
  "audio_wrong_device": ErrorSpec(
    "audio_wrong_device",
    "Hang",
    "Rossz vagy hiányzó hang eszköz beállítás",
    "Hangkártya menü → bemenet/kimenet; Line-in port alkalmaz",
  ),
  # --- RX / dekód (3) ---
  "rx_start_fail": ErrorSpec(
    "rx_start_fail",
    "RX",
    "A vétel (RX motor) nem indult el",
    "Hangbemenet, PulseAudio; Indítás újra; nézd a gui_nohup.log-ot",
  ),
  "rx_decode_stall": ErrorSpec(
    "rx_decode_stall",
    "RX",
    "Nincs új FT8 dekód hosszú ideje (vétel stall)",
    "Ellenőrizd a kábelt, szintet, sávot; RX újraindítás (Stop → Indítás)",
  ),
  "rx_linein_port": ErrorSpec(
    "rx_linein_port",
    "RX",
    "Line-in port beállítása sikertelen",
    "Hangkártya menü → válaszd a line-in bemenetet; pactl set-source-port",
  ),
  # --- Rendszer (2) ---
  "system_port_busy": ErrorSpec(
    "system_port_busy",
    "Rendszer",
    "A soros port foglalt (/dev/ttyUSB0)",
    "Zárd be a másik programot; csak egy folyamat használja az ESP32-t",
  ),
  "system_io_overload": ErrorSpec(
    "system_io_overload",
    "Rendszer",
    "A rendszer I/O túlterhelt (hang + pactl + USB)",
    "Ne futtass párhuzamos tesztet; várj; szükség esetén reboot",
  ),
}

ALL_CODES: tuple[str, ...] = tuple(CATALOG.keys())


def classify_tx_error(error: str) -> str | None:
  e = (error or "").strip()
  if not e:
    return None
  upper = e.upper()
  if "SAFETY_LOCK" in upper:
    return "esp_safety_lock"
  if "WARN PTT_STUCK" in upper:
    return "esp_ptt_stuck_warn"
  if "PTT 0" in e or "ptt_off" in e.lower():
    return "esp_ptt_off_fail"
  if "PTT 1" in e or "PTT_ON" in upper or "ptt_on_failed" in e:
    return "esp_ptt_on_fail"
  if "aborted" in e.lower():
    return "tx_aborted"
  if "cancelled" in e.lower():
    return "tx_cancelled"
  if "encode_failed" in e:
    return "tx_encode_fail"
  if "underrun" in e.lower():
    return "audio_underrun"
  if "AUDIO_FAIL" in upper or "alsa" in e.lower():
    return "tx_audio_fail"
  if any(x in e.lower() for x in ("busy", "permission denied", "could not open port")):
    return "system_port_busy"
  if any(x in e.lower() for x in ("no such file", "ttyusb", "serial")):
    return "esp_usb_serial"
  return None
