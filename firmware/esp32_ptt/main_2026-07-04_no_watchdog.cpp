/*
 * FT-817 DATA PTT — 2026-07-04 11:41 feltöltött verzió (archívum)
 * GPIO26 → opto → DATA pin 2+3
 *
 * Parancsok: PING, TIME, PTT 1/0, AT <unix_ms> 1|0, STATUS (PTT= csak)
 * Nincs: SHUTDOWN, RESUME, LOCK, 20 s watchdog
 *
 * Feltöltés: ./scripts/flash_hold_boot.sh (BOOT tartva, default_reset @ 115200)
 */

#include <Arduino.h>
#include <sys/time.h>

constexpr uint8_t PIN_PTT = 26;
constexpr size_t LINE_MAX = 96;

static bool g_ptt = false;
static int64_t g_sched_at = 0;
static int g_sched_val = -1;

static int64_t nowUnixMs() {
  struct timeval tv {};
  gettimeofday(&tv, nullptr);
  return static_cast<int64_t>(tv.tv_sec) * 1000 + tv.tv_usec / 1000;
}

static void setPtt(bool on) {
  g_ptt = on;
  digitalWrite(PIN_PTT, on ? HIGH : LOW);
}

static void applySchedule() {
  if (g_sched_val < 0) return;
  const int64_t now = nowUnixMs();
  if (now >= g_sched_at) {
    setPtt(g_sched_val != 0);
    g_sched_val = -1;
    Serial.printf("OK SCHED PTT=%d at=%lld\n", g_ptt ? 1 : 0, static_cast<long long>(g_sched_at));
  }
}

static bool setTimeUnixMs(int64_t unix_ms) {
  struct timeval tv {};
  tv.tv_sec = static_cast<time_t>(unix_ms / 1000);
  tv.tv_usec = static_cast<suseconds_t>((unix_ms % 1000) * 1000);
  return settimeofday(&tv, nullptr) == 0;
}

static void handleLine(char* line) {
  while (*line == ' ' || *line == '\t') ++line;
  if (*line == '\0') return;

  if (strcmp(line, "PING") == 0) { Serial.println("PONG"); return; }
  if (strcmp(line, "STATUS") == 0) {
    Serial.printf("TIME=%lld PTT=%d\n", static_cast<long long>(nowUnixMs()), g_ptt ? 1 : 0);
    return;
  }
  if (strncmp(line, "TIME ", 5) == 0) {
    const int64_t t = strtoll(line + 5, nullptr, 10);
    if (setTimeUnixMs(t)) Serial.printf("OK TIME %lld\n", static_cast<long long>(t));
    else Serial.println("ERR TIME");
    return;
  }
  if (strcmp(line, "PTT 1") == 0 || strcmp(line, "PTT ON") == 0) {
    setPtt(true); Serial.println("OK PTT 1"); return;
  }
  if (strcmp(line, "PTT 0") == 0 || strcmp(line, "PTT OFF") == 0) {
    setPtt(false); Serial.println("OK PTT 0"); return;
  }
  if (strncmp(line, "AT ", 3) == 0) {
    char* p = line + 3;
    char* sp = strchr(p, ' ');
    if (!sp) { Serial.println("ERR AT args"); return; }
    *sp = '\0';
    g_sched_at = strtoll(p, nullptr, 10);
    g_sched_val = (atoi(sp + 1) != 0) ? 1 : 0;
    Serial.printf("ARM AT %lld %d\n", static_cast<long long>(g_sched_at), g_sched_val);
    return;
  }
  Serial.println("ERR unknown");
}

void setup() {
  pinMode(PIN_PTT, OUTPUT);
  setPtt(false);
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("FT8_PTT gpio=26 ready");
}

void loop() {
  applySchedule();
  static char buf[LINE_MAX];
  static size_t n = 0;
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\r') continue;
    if (c == '\n') { buf[n] = '\0'; handleLine(buf); n = 0; continue; }
    if (n + 1 < LINE_MAX) buf[n++] = c;
  }
}
