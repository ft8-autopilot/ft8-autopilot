/*
 * FT-817 DATA PTT — GPIO26 → opto → DATA pin 2+3
 * Soros vezérlés 115200: PC időszinkron + ütemezett PTT
 *
 * Parancsok (soronként, \n):
 *   PING              → PONG
 *   TIME <unix_ms>    → ESP óra beállítás (PC NTP)
 *   PTT 1 | PTT 0     → azonnali PTT ON/OFF
 *   SHUTDOWN          → PTT OFF + biztonsági tiltás (PTT 1 → ERR)
 *   RESUME            → tiltás feloldása, PTT OFF
 *   AT <unix_ms> 1|0  → PTT állapot unix_ms időpontban (ms)
 *   STATUS            → TIME=<ms> PTT=0|1 LOCK=0|1
 *
 * Hardver watchdog: 20 s folyamatos PTT → automatikus PTT OFF + SAFETY_LOCK
 */

#include <Arduino.h>
#include <sys/time.h>

constexpr uint8_t PIN_PTT = 26;
constexpr size_t kLineMax = 96;
constexpr uint32_t kMaxPttHoldMs = 20000;  // 20 s — FT8 slot ~12.6 s + margó

static bool g_ptt = false;
static bool g_safety_lock = false;
static int64_t g_sched_at = 0;
static int g_sched_val = -1;  // 0=OFF 1=ON -1=none
static uint32_t g_ptt_on_since_ms = 0;  // millis() — független PC-től

static int64_t nowUnixMs() {
  struct timeval tv {};
  gettimeofday(&tv, nullptr);
  return static_cast<int64_t>(tv.tv_sec) * 1000 + tv.tv_usec / 1000;
}

static void setPtt(bool on) {
  g_ptt = on;
  digitalWrite(PIN_PTT, on ? HIGH : LOW);
  g_ptt_on_since_ms = on ? millis() : 0;
}

static void tripPttWatchdog() {
  setPtt(false);
  g_sched_val = -1;
  g_safety_lock = true;
  Serial.println("WARN PTT_STUCK 20s");
  Serial.println("OK PTT 0");
}

static void checkPttWatchdog() {
  if (!g_ptt || g_ptt_on_since_ms == 0) {
    return;
  }
  const uint32_t elapsed = millis() - g_ptt_on_since_ms;
  if (elapsed >= kMaxPttHoldMs) {
    tripPttWatchdog();
  }
}

static void applySchedule() {
  if (g_sched_val < 0) {
    return;
  }
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
  while (*line == ' ' || *line == '\t') {
    ++line;
  }
  if (*line == '\0') {
    return;
  }

  if (strcmp(line, "PING") == 0) {
    Serial.println("PONG");
    return;
  }
  if (strcmp(line, "STATUS") == 0) {
    Serial.printf(
      "TIME=%lld PTT=%d LOCK=%d\n",
      static_cast<long long>(nowUnixMs()),
      g_ptt ? 1 : 0,
      g_safety_lock ? 1 : 0);
    return;
  }

  if (strcmp(line, "SHUTDOWN") == 0) {
    g_sched_val = -1;
    g_safety_lock = true;
    setPtt(false);
    Serial.println("OK SHUTDOWN");
    return;
  }
  if (strcmp(line, "RESUME") == 0) {
    g_safety_lock = false;
    setPtt(false);
    Serial.println("OK RESUME");
    return;
  }

  if (strncmp(line, "TIME ", 5) == 0) {
    const int64_t t = strtoll(line + 5, nullptr, 10);
    if (setTimeUnixMs(t)) {
      Serial.printf("OK TIME %lld\n", static_cast<long long>(t));
    } else {
      Serial.println("ERR TIME");
    }
    return;
  }

  if (strcmp(line, "PTT 1") == 0 || strcmp(line, "PTT ON") == 0) {
    if (g_safety_lock) {
      Serial.println("ERR SAFETY_LOCK");
      return;
    }
    setPtt(true);
    Serial.println("OK PTT 1");
    return;
  }
  if (strcmp(line, "PTT 0") == 0 || strcmp(line, "PTT OFF") == 0) {
    setPtt(false);
    Serial.println("OK PTT 0");
    return;
  }

  if (strncmp(line, "AT ", 3) == 0) {
    char* p = line + 3;
    char* sp = strchr(p, ' ');
    if (!sp) {
      Serial.println("ERR AT args");
      return;
    }
    *sp = '\0';
    const int64_t at = strtoll(p, nullptr, 10);
    const int val = atoi(sp + 1);
    g_sched_at = at;
    g_sched_val = (val != 0) ? 1 : 0;
    Serial.printf("ARM AT %lld %d\n", static_cast<long long>(at), g_sched_val);
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
  Serial.printf("BOOT_MS=%lld\n", static_cast<long long>(nowUnixMs()));
}

void loop() {
  applySchedule();
  checkPttWatchdog();

  static char buf[kLineMax];
  static size_t n = 0;

  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      buf[n] = '\0';
      handleLine(buf);
      n = 0;
      continue;
    }
    if (n + 1 < kLineMax) {
      buf[n++] = c;
    }
  }
}
