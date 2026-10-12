// Ядро самодельного CAN-адаптера: команды ELM327 + ISO-TP (ISO 15765-2).
//
// Чистый C++ без Arduino: тот же файл собирается и в прошивку ESP32, и в тестовую программу
// для компьютера (tests/can_sim.cpp), поэтому разбор команд и сборку длинных ответов можно
// проверить без машины.
//
// В отличие от дешёвого клона ELM327 здесь действительно работают:
//   ATSH xxx      — свой CAN ID запроса (0x7E0, 0x7E1, 0x740, ...);
//   ATCRA [xxx]   — фильтр ID ответа;
//   ATFCSH/ATFCSD/ATFCSM — Flow Control, без которого блок не досылает длинный ответ;
//   ATMA          — пассивное прослушивание шины.
// K-line здесь нет: только CAN 11 бит 500 кбит/с (протокол 6, контакты OBD 6/14).

#pragma once

#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ELM_CAN_VERSION "ELM327 v1.5 (vito-can 0.1)"

// Размеры под память платы. На Arduino Uno (2 КБ ОЗУ) прошивка задаёт их меньше до #include.
#ifndef ELM_MAX_PAYLOAD
#define ELM_MAX_PAYLOAD 4095  // предел ISO-TP
#endif
#ifndef ELM_MAX_SESSIONS
#define ELM_MAX_SESSIONS 2    // одновременно собираемых длинных ответов
#endif
#ifndef ELM_LINE_MAX
#define ELM_LINE_MAX 128      // длина команды
#endif
// Минимальная пауза между кадрами длинного ответа (STmin в Flow Control), мс. Медленной плате
// нужна передышка: у MCP2515 всего два приёмных буфера.
#ifndef ELM_MIN_STMIN
#define ELM_MIN_STMIN 0
#endif

struct CanFrame {
  uint32_t id = 0;
  bool ext = false;   // 29-битный ID (на OBD Mercedes W639 не встречается, только для ATMA)
  uint8_t len = 0;
  uint8_t data[8] = {0};
};

// Всё, что зависит от железа.
class ElmCanIo {
 public:
  virtual ~ElmCanIo() {}
  virtual void write(const char *s) = 0;
  virtual bool canWrite(size_t) { return true; }       // хватит ли места в буфере порта
  virtual bool canSend(const CanFrame &f) = 0;         // false — кадр не ушёл (нет шины)
  virtual bool canReceive(CanFrame &f, uint32_t timeoutMs) = 0;
  virtual uint32_t now() = 0;                          // миллисекунды
  virtual bool inputPending() = 0;                     // пришёл символ с ПК (останавливает ATMA)
  virtual void discardInput() = 0;                     // выбросить его (как ELM327)
  // true — только слушать: не подтверждать чужие кадры и ничего не передавать.
  virtual void setListenOnly(bool on) = 0;
  virtual float batteryVolts() { return -1; }          // < 0 — не измеряется
};

class ElmCan {
 public:
  static const int MAX_PAYLOAD = ELM_MAX_PAYLOAD;
  static const int MAX_SESSIONS = ELM_MAX_SESSIONS;
  static const uint32_t PENDING_MS = 5000;  // ждать после 7F xx 78 («ответ будет позже»)

  explicit ElmCan(ElmCanIo &io) : io_(io) { defaults(); }

  // Одна строка от ПК (без '\r'). Пустая строка повторяет предыдущую команду, как у ELM327.
  void handleLine(const char *raw) {
    char line[ELM_LINE_MAX];
    size_t n = 0;
    for (const char *p = raw; *p && n < sizeof(line) - 1; p++) {
      if (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') continue;
      line[n++] = (char)toupper((unsigned char)*p);
    }
    line[n] = 0;
    if (echo_) {
      io_.write(raw);
      io_.write(eol());
    }
    if (n == 0) {
      strcpy(line, last_);
    } else {
      strcpy(last_, line);
    }
    if (line[0] == 'A' && line[1] == 'T') {
      handleAt(line + 2);
    } else if (line[0]) {
      handleData(line);
    }
    io_.write(eol());
    io_.write(">");
  }

 private:
  struct Session {
    bool used = false;
    uint32_t id = 0;
    uint16_t total = 0;
    uint16_t have = 0;
    uint8_t nextSn = 1;
    uint8_t sinceFc = 0;
    uint8_t buf[MAX_PAYLOAD];
  };

  ElmCanIo &io_;
  bool echo_, linefeeds_, spaces_, headers_, caf_, monitorDlc_;
  char protocol_;          // '0' (авто) или '6'; остальное — нет связи
  bool autoProtocol_;
  uint32_t header_;
  bool headerSet_;
  int32_t cra_;            // -1 — фильтр не задан
  uint32_t timeoutMs_;
  int32_t fcHeader_;       // -1 — по умолчанию
  uint8_t fcData_[8];
  uint8_t fcLen_;
  int fcMode_;
  char last_[ELM_LINE_MAX] = "";
  Session sessions_[MAX_SESSIONS];

  void defaults() {
    echo_ = true;
    linefeeds_ = false;
    spaces_ = true;
    headers_ = false;
    caf_ = true;
    monitorDlc_ = false;
    protocol_ = '0';
    autoProtocol_ = true;
    header_ = 0x7DF;
    headerSet_ = false;
    cra_ = -1;
    timeoutMs_ = 0x32 * 4;
    fcHeader_ = -1;
    fcData_[0] = 0x30;
    fcData_[1] = 0x00;
    fcData_[2] = 0x00;
    fcLen_ = 3;
    fcMode_ = 0;
    for (auto &s : sessions_) s.used = false;
    io_.setListenOnly(true);  // пока ничего не спросили — ничего и не передаём
  }

  const char *eol() const { return linefeeds_ ? "\r\n" : "\r"; }

  void line(const char *s) {
    io_.write(s);
    io_.write(eol());
  }

  static int hexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  }

  static bool parseHex(const char *s, size_t len, uint32_t &out) {
    if (len == 0 || len > 8) return false;
    out = 0;
    for (size_t i = 0; i < len; i++) {
      int v = hexVal(s[i]);
      if (v < 0) return false;
      out = (out << 4) | (uint32_t)v;
    }
    return true;
  }

  static bool startsWith(const char *s, const char *p) { return strncmp(s, p, strlen(p)) == 0; }

  bool canProtocol() const { return protocol_ == '0' || protocol_ == '6'; }

  // ---- AT-команды --------------------------------------------------------

  void handleAt(const char *c) {
    size_t n = strlen(c);
    uint32_t v;
    if (!strcmp(c, "Z") || !strcmp(c, "WS")) {
      defaults();
      io_.write(eol());
      line(ELM_CAN_VERSION);
    } else if (!strcmp(c, "I")) {
      line(ELM_CAN_VERSION);
    } else if (!strcmp(c, "@1")) {
      line("vito-can: ESP32 TWAI, CAN 11bit 500k");
    } else if (!strcmp(c, "D")) {
      defaults();
      line("OK");
    } else if ((c[0] == 'E' || c[0] == 'L' || c[0] == 'S' || c[0] == 'H') && n == 2 &&
               (c[1] == '0' || c[1] == '1')) {
      bool on = c[1] == '1';
      if (c[0] == 'E') echo_ = on;
      if (c[0] == 'L') linefeeds_ = on;
      if (c[0] == 'S') spaces_ = on;
      if (c[0] == 'H') headers_ = on;
      line("OK");
    } else if (!strcmp(c, "D0") || !strcmp(c, "D1")) {
      monitorDlc_ = c[1] == '1';
      line("OK");
    } else if (!strcmp(c, "CAF0") || !strcmp(c, "CAF1")) {
      caf_ = c[3] == '1';
      line("OK");
    } else if (startsWith(c, "SP") || startsWith(c, "TP")) {
      const char *p = c + 2;
      bool a = *p == 'A';
      if (a) p++;
      if (strlen(p) != 1 || hexVal(*p) < 0) return line("?");
      protocol_ = *p;
      autoProtocol_ = a || *p == '0';
      line("OK");
    } else if (!strcmp(c, "DP")) {
      line(canProtocol() ? (autoProtocol_ ? "AUTO, ISO 15765-4 (CAN 11/500)" : "ISO 15765-4 (CAN 11/500)")
                         : "NONE (vito-can: only CAN 11/500)");
    } else if (!strcmp(c, "DPN")) {
      line(canProtocol() ? (autoProtocol_ ? "A6" : "6") : "0");
    } else if (!strcmp(c, "PC")) {
      for (auto &s : sessions_) s.used = false;
      io_.setListenOnly(true);
      line("OK");
    } else if (startsWith(c, "ST") && n == 4 && parseHex(c + 2, 2, v)) {
      timeoutMs_ = v ? v * 4 : 0x32 * 4;
      line("OK");
    } else if (startsWith(c, "SH") && (n == 5 || n == 8 || n == 10) && parseHex(c + 2, n - 2, v)) {
      // xxx; xxyyzz (11 бит: берутся последние 3 цифры); xxxxxxxx (29 бит — не поддерживается)
      if (n == 10) return line("?");
      header_ = v & 0x7FF;
      headerSet_ = true;
      line("OK");
    } else if (!strcmp(c, "CRA")) {
      cra_ = -1;
      line("OK");
    } else if (startsWith(c, "CRA") && n == 6 && parseHex(c + 3, 3, v)) {
      cra_ = (int32_t)v;
      line("OK");
    } else if (startsWith(c, "FCSH") && n == 7 && parseHex(c + 4, 3, v)) {
      fcHeader_ = (int32_t)v;
      line("OK");
    } else if (startsWith(c, "FCSD") && n >= 6 && n <= 20 && n % 2 == 0) {
      uint8_t tmp[8];
      size_t k = (n - 4) / 2;
      for (size_t i = 0; i < k; i++) {
        if (!parseHex(c + 4 + 2 * i, 2, v)) return line("?");
        tmp[i] = (uint8_t)v;
      }
      memcpy(fcData_, tmp, k);
      fcLen_ = (uint8_t)k;
      line("OK");
    } else if (startsWith(c, "FCSM") && n == 5 && c[4] >= '0' && c[4] <= '2') {
      fcMode_ = c[4] - '0';
      line("OK");
    } else if (!strcmp(c, "MA")) {
      monitor();
    } else if (!strcmp(c, "RV")) {
      float volts = io_.batteryVolts();
      if (volts < 0) return line("?");
      char buf[16];
      int tenths = (int)(volts * 10 + 0.5f);  // без %f: на AVR printf не умеет float
      snprintf(buf, sizeof(buf), "%d.%dV", tenths / 10, tenths % 10);
      line(buf);
    } else if (!strcmp(c, "FI") || !strcmp(c, "SI")) {
      line("BUS INIT: ...ERROR");  // K-line нет
    } else if (startsWith(c, "AT") || startsWith(c, "KW") || startsWith(c, "IIA") ||
               startsWith(c, "WM") || startsWith(c, "AL") || startsWith(c, "M0") || startsWith(c, "M1")) {
      line("OK");  // параметры, которые здесь ни на что не влияют
    } else {
      line("?");
    }
  }

  // ---- вывод -------------------------------------------------------------

  void appendByte(char *buf, size_t size, uint8_t b) {
    size_t len = strlen(buf);
    snprintf(buf + len, size - len, spaces_ && len ? " %02X" : "%02X", b);
  }

  void printFrame(const CanFrame &f, int from, int count, bool withHeader) {
    char buf[64] = "";
    if (withHeader) {
      if (f.ext) {
        snprintf(buf, sizeof(buf), "%08X", (unsigned)f.id);
      } else {
        snprintf(buf, sizeof(buf), "%03X", (unsigned)f.id);
      }
      if (monitorDlc_) {
        size_t len = strlen(buf);
        snprintf(buf + len, sizeof(buf) - len, spaces_ ? " %u" : "%u", f.len);
      }
    }
    for (int i = from; i < from + count && i < f.len; i++) appendByte(buf, sizeof(buf), f.data[i]);
    line(buf);
  }

  // Длинный ответ в формате ELM327 без заголовков: "014", "0: 5A 86 ...", "1: ...".
  void printAssembled(const Session &s) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%03X", s.total);
    line(buf);
    int idx = 0;
    int have = s.have < MAX_PAYLOAD ? s.have : MAX_PAYLOAD;
    for (int pos = 0; pos < have; idx++) {
      int chunk = pos == 0 ? 6 : 7;
      snprintf(buf, sizeof(buf), "%X:", idx & 0xF);
      for (int i = pos; i < pos + chunk && i < have; i++) {
        size_t len = strlen(buf);
        snprintf(buf + len, sizeof(buf) - len, spaces_ ? " %02X" : "%02X", s.buf[i]);
      }
      line(buf);
      pos += chunk;
    }
  }

  // ---- запрос -----------------------------------------------------------

  bool acceptReply(const CanFrame &f) const {
    if (f.ext) return false;
    if (cra_ >= 0) return f.id == (uint32_t)cra_;
    if (header_ == 0x7DF || (header_ >= 0x7E0 && header_ <= 0x7E7)) return f.id >= 0x7E8 && f.id <= 0x7EF;
    if (f.id == header_ || f.len < 2) return false;
    // Фильтра нет: на шине могут ходить чужие кадры. Пропускаем только похожие на ответ
    // диагностики: одиночный кадр с кодом ответа (0x40..0x7F) или начало/продолжение ISO-TP.
    uint8_t pci = f.data[0] >> 4;
    if (pci == 0) return (f.data[0] & 0x0F) >= 1 && (f.data[0] & 0x0F) <= 7 && (f.data[1] & 0xC0) == 0x40;
    return pci == 1 || pci == 2;
  }

  uint32_t fcId(uint32_t replyId) const {
    if (fcMode_ == 1 && fcHeader_ >= 0) return (uint32_t)fcHeader_;
    if (replyId >= 0x7E8 && replyId <= 0x7EF) return replyId - 8;
    return header_;
  }

  bool sendFlowControl(uint32_t replyId) {
    CanFrame fc;
    fc.id = fcId(replyId);
    fc.len = 8;
    if (fcMode_ == 1) {
      memcpy(fc.data, fcData_, fcLen_);
    } else {
      fc.data[0] = 0x30;  // продолжай; размер блока 0 — всё сразу
    }
    if (fc.data[2] < ELM_MIN_STMIN) fc.data[2] = ELM_MIN_STMIN;
    return io_.canSend(fc);
  }

  Session *findSession(uint32_t id) {
    for (auto &s : sessions_)
      if (s.used && s.id == id) return &s;
    return nullptr;
  }

  void handleData(const char *hex) {
    size_t n = strlen(hex);
    int maxReplies = 0;
    if (n % 2) {  // ELM327: последняя нечётная цифра — сколько ответов ждать
      maxReplies = hexVal(hex[n - 1]);
      n--;
    }
    size_t count = n / 2;
    if (count == 0 || count > (caf_ ? 7u : 8u)) return line("?");
    CanFrame req;
    req.id = header_;
    req.len = 8;
    size_t at = 0;
    if (caf_) req.data[at++] = (uint8_t)count;
    for (size_t i = 0; i < count; i++) {
      int hi = hexVal(hex[2 * i]), lo = hexVal(hex[2 * i + 1]);
      if (hi < 0 || lo < 0) return line("?");
      req.data[at++] = (uint8_t)(hi << 4 | lo);
    }
    if (!caf_) req.len = (uint8_t)count;
    if (!canProtocol()) return line("UNABLE TO CONNECT");

    for (auto &s : sessions_) s.used = false;
    io_.setListenOnly(false);
    if (!io_.canSend(req)) {
      io_.setListenOnly(true);
      return line("CAN ERROR");
    }

    int replies = 0;
    bool printed = false;
    uint32_t deadline = io_.now() + timeoutMs_;
    CanFrame f;
    while ((int32_t)(deadline - io_.now()) > 0) {
      uint32_t left = deadline - io_.now();
      if (!io_.canReceive(f, left < 20 ? left : 20)) continue;
      if (!acceptReply(f)) continue;
      if (!caf_) {
        printFrame(f, 0, f.len, headers_);
        printed = true;
        deadline = io_.now() + timeoutMs_;
        if (maxReplies && ++replies >= maxReplies) break;
        continue;
      }
      uint8_t pci = f.data[0] >> 4;
      if (pci == 0) {
        uint8_t len = f.data[0] & 0x0F;
        if (len == 0 || len > 7 || len >= f.len) continue;
        if (len >= 3 && f.data[1] == 0x7F && f.data[3] == 0x78) {
          deadline = io_.now() + PENDING_MS;  // блок занят, ответ придёт позже
          continue;
        }
        if (headers_) {
          printFrame(f, 0, len + 1, true);
        } else {
          printFrame(f, 1, len, false);
        }
        printed = true;
        deadline = io_.now() + timeoutMs_;
        if (maxReplies && ++replies >= maxReplies) break;
      } else if (pci == 1) {
        uint16_t total = (uint16_t)((f.data[0] & 0x0F) << 8 | f.data[1]);
        if (total < 8 || f.len < 8) continue;
        Session *s = findSession(f.id);
        for (int i = 0; !s && i < MAX_SESSIONS; i++)
          if (!sessions_[i].used) s = &sessions_[i];
        if (!s) continue;
        s->used = true;
        s->id = f.id;
        s->total = total;
        s->have = 6;
        s->nextSn = 1;
        s->sinceFc = 0;
        memcpy(s->buf, f.data + 2, 6);
        if (headers_) {
          printFrame(f, 0, 8, true);
          printed = true;
        }
        if (!sendFlowControl(f.id)) {
          io_.setListenOnly(true);
          return line("CAN ERROR");
        }
        deadline = io_.now() + timeoutMs_;
      } else if (pci == 2) {
        Session *s = findSession(f.id);
        if (!s) continue;
        if ((f.data[0] & 0x0F) != s->nextSn) {
          // Пропущен кадр: печатаем, что успели собрать, — заявленная длина больше
          // полученных данных, vito_diag отметит ответ как обрезанный.
          if (!headers_) printAssembled(*s);
          printed = true;
          s->used = false;
          continue;
        }
        s->nextSn = (s->nextSn + 1) & 0x0F;
        int chunk = s->total - s->have < 7 ? s->total - s->have : 7;
        for (int i = 0; i < chunk; i++) {
          // Не влезло в память платы — дальше только считаем; напечатается заявленная длина
          // и то, что поместилось, и vito_diag увидит обрезанный ответ.
          if (s->have + i < MAX_PAYLOAD) s->buf[s->have + i] = f.data[1 + i];
        }
        s->have += chunk;
        if (headers_) {
          printFrame(f, 0, f.len, true);
          printed = true;
        }
        deadline = io_.now() + timeoutMs_;
        if (s->have >= s->total) {
          if (!headers_) printAssembled(*s);
          printed = true;
          s->used = false;
          if (maxReplies && ++replies >= maxReplies) break;
        } else if (fcMode_ == 1 && fcLen_ >= 2 && fcData_[1] && ++s->sinceFc >= fcData_[1]) {
          s->sinceFc = 0;  // размер блока исчерпан — разрешаем следующий
          sendFlowControl(f.id);
        }
      }
    }
    for (auto &s : sessions_) {
      if (s.used && !headers_) {
        printAssembled(s);  // ответ не дошёл до конца
        printed = true;
      }
      s.used = false;
    }
    if (!printed) line("NO DATA");
  }

  // ---- ATMA -------------------------------------------------------------

  void monitor() {
    CanFrame f;
    while (!io_.inputPending()) {
      if (!io_.canReceive(f, 20)) continue;
      if (!io_.canWrite(64)) {
        line("BUFFER FULL");
        return;
      }
      printFrame(f, 0, f.len, true);  // монитору всегда нужен ID
    }
    io_.discardInput();  // символ, остановивший мониторинг, командой не считается
  }
};
