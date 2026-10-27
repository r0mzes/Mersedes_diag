// Ядро приборки «Января» для ESP32: перенос vito_diag (elm.py K-line, january.py, live.py Poller)
// на C++ без Arduino — собирается и для ПК (tests/lada_sim.cpp), где сверяется с Python.
//
// Через интерфейс Io идут команды ELM327 (адаптер по Bluetooth на ESP32, эмулятор на ПК).
// Только чтение: request() пропускает лишь службы чтения (как protocol.is_read_only).
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "dtc_table.h"

namespace lada {

using Bytes = std::vector<int>;

struct Io {
  virtual ~Io() {}
  // Команда ELM327 -> ответ как есть (до '>'). wait_ms — сколько ждать ответа.
  virtual std::string cmd(const std::string &c, unsigned wait_ms) = 0;
  virtual double now() = 0;           // секунды
  virtual void sleep(double sec) = 0;
};

struct DiagError : std::runtime_error {
  int nrc;
  explicit DiagError(const std::string &m, int n = -1) : std::runtime_error(m), nrc(n) {}
};

// ---- разбор ответов (protocol.py) ------------------------------------------

inline std::string hex2(int b) {
  char s[4];
  snprintf(s, sizeof s, "%02X", b & 0xFF);
  return s;
}

inline std::string hexBytes(const Bytes &d) {
  std::string out;
  for (size_t i = 0; i < d.size(); i++) out += (i ? " " : "") + hex2(d[i]);
  return out;
}

inline std::string upper(std::string s) {
  for (auto &c : s) if (c >= 'a' && c <= 'z') c -= 32;
  return s;
}

inline std::string trim(const std::string &s) {
  size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
  return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}

inline bool startsWith(const std::string &s, const std::string &p) { return s.compare(0, p.size(), p) == 0; }
inline bool endsWith(const std::string &s, const std::string &p) {
  return s.size() >= p.size() && s.compare(s.size() - p.size(), p.size(), p) == 0;
}

inline std::vector<std::string> cleanLines(const std::string &text) {
  static const char *ERRORS[] = {"NO DATA", "CAN ERROR", "BUS INIT", "BUS ERROR", "UNABLE TO CONNECT",
                                 "STOPPED", "?", "BUFFER FULL", "FB ERROR", "DATA ERROR", "ACT ALERT",
                                 "LV RESET"};
  std::vector<std::string> lines;
  std::string cur;
  for (size_t i = 0; i <= text.size(); i++) {
    char c = i < text.size() ? text[i] : '\n';
    if (c == '\r' || c == '\n') {
      std::string l = trim(cur);
      cur.clear();
      std::string u = upper(l);
      if (l.empty() || l == ">" || startsWith(u, "SEARCHING")) continue;
      if (startsWith(u, "BUS INIT") && endsWith(u, "OK")) continue;
      lines.push_back(l);
    } else {
      cur += c;
    }
  }
  for (auto &l : lines) {
    std::string u = upper(l);
    for (const char *e : ERRORS) {
      if (startsWith(u, e)) {
        std::string all;
        for (size_t i = 0; i < lines.size(); i++) all += (i ? " / " : "") + lines[i];
        throw DiagError(all);
      }
    }
  }
  return lines;
}

// "83 F1 10 7E 01 xx" -> байты; false — в строке не только hex.
inline bool parseHexLine(const std::string &line, Bytes &out) {
  out.clear();
  std::string tok;
  for (size_t i = 0; i <= line.size(); i++) {
    char c = i < line.size() ? line[i] : ' ';
    if (c == ' ' || c == '\t') {
      if (tok.empty()) continue;
      if (tok.size() > 2) return false;
      for (char t : tok)
        if (!isxdigit((unsigned char)t)) return false;
      out.push_back((int)strtol(tok.c_str(), nullptr, 16));
      tok.clear();
    } else {
      tok += c;
    }
  }
  return true;
}

inline std::vector<Bytes> parseKline(const std::string &text) {
  std::vector<Bytes> messages;
  for (auto &line : cleanLines(text)) {
    Bytes b;
    if (!parseHexLine(line, b) || b.empty()) continue;
    int fmt = b[0];
    size_t idx = 1;
    if (fmt & 0x80) idx += 2;
    size_t length = fmt & 0x3F;
    if (length == 0 && b.size() > idx) length = b[idx++];
    Bytes data;
    if (length) {
      for (size_t i = idx; i < idx + length && i < b.size(); i++) data.push_back(b[i]);
    } else {
      for (size_t i = idx; i + 1 < b.size(); i++) data.push_back(b[i]);
    }
    messages.push_back(data);
  }
  return messages;
}

inline int klineSource(const std::string &text) {
  for (auto &line : cleanLines(text)) {
    Bytes b;
    if (!parseHexLine(line, b)) continue;
    if (b.size() >= 3 && (b[0] & 0x80)) return b[2];
  }
  return -1;
}

inline Bytes finalPayload(const std::vector<Bytes> &msgs, int service) {
  std::vector<const Bytes *> pos;
  for (auto &m : msgs)
    if (m.size() >= 2 && m[0] == service + 0x40) pos.push_back(&m);
  if (pos.size() > 1) {
    bool same = true;
    for (auto *m : pos) same = same && (*m)[1] == (*pos[0])[1];
    if (same) {
      Bytes out = {(*pos[0])[0], (*pos[0])[1]};
      for (auto *m : pos) out.insert(out.end(), m->begin() + 2, m->end());
      return out;
    }
  }
  for (auto it = msgs.rbegin(); it != msgs.rend(); ++it) {
    const Bytes &m = *it;
    if (m.size() >= 3 && m[0] == 0x7F && m[2] == 0x78) continue;
    if (!m.empty() && (m[0] == service + 0x40 || m[0] == 0x7F)) return m;
  }
  return msgs.empty() ? Bytes{} : msgs.back();
}

inline const char *nrcText(int n) {
  switch (n) {
    case 0x10: return "общий отказ";
    case 0x11: return "сервис не поддерживается";
    case 0x12: return "подфункция не поддерживается";
    case 0x13: return "неверная длина/формат";
    case 0x21: return "занят, повторите";
    case 0x22: return "условия не выполнены";
    case 0x31: return "параметр вне диапазона";
    case 0x33: return "доступ запрещён (нужен ключ)";
    case 0x78: return "ответ будет позже";
    case 0x7E: return "подфункция не поддерживается в этой сессии";
    case 0x7F: return "сервис не поддерживается в этой сессии";
  }
  return "неизвестно";
}

inline const Bytes &checkPositive(const Bytes &p, int service) {
  if (p.size() >= 3 && p[0] == 0x7F)
    throw DiagError("Блок отклонил запрос 0x" + hex2(service) + ": NRC 0x" + hex2(p[2]) + " (" +
                        nrcText(p[2]) + ")", p[2]);
  if (p.empty() || p[0] != service + 0x40) throw DiagError("Неожиданный ответ: " + hexBytes(p));
  return p;
}

inline bool isReadOnly(const Bytes &r) {
  if (r.empty()) return false;
  int s = r[0];
  if (s == 0x10) return r.size() > 1 && (r[1] == 0x01 || r[1] == 0x81);
  static const int OK[] = {0x01, 0x02, 0x03, 0x05, 0x06, 0x07, 0x09, 0x0A, 0x12, 0x17,
                           0x18, 0x19, 0x1A, 0x21, 0x22, 0x23, 0x3E};
  for (int o : OK)
    if (s == o) return true;
  return false;
}

inline std::string extractText(const Bytes &p, size_t from) {
  std::string out, word;
  for (size_t i = from; i <= p.size(); i++) {
    int b = i < p.size() ? p[i] : 32;
    if (b > 32 && b < 127) {
      word += (char)b;
    } else {
      if (word.size() >= 4) out += (out.empty() ? "" : " ") + word;
      word.clear();
    }
  }
  return out;
}

inline std::string dtcCode(int b1, int b2) {
  char s[8];
  snprintf(s, sizeof s, "%c%d%X%02X", "PCBU"[(b1 >> 6) & 3], (b1 >> 4) & 3, b1 & 0x0F, b2 & 0xFF);
  return s;
}

inline std::string kwpStatus(int st) {
  static const char *storage[] = {"нет", "текущая (не подтверждена)", "сохранена, сейчас нет",
                                  "сохранена и присутствует"};
  std::string out = std::string("состояние: ") + storage[(st >> 5) & 3];
  if (st & 0x80) out += ", горит лампа";
  return out;
}

inline std::string dtcDescription(const std::string &code) {
  for (const DtcEntry &e : DTC_TABLE)
    if (code.compare(0, 5, e.code) == 0) return e.description;
  std::string d;
  switch (code[0]) {
    case 'P': d = "Силовой агрегат (двигатель, КПП)"; break;
    case 'C': d = "Шасси (ABS, ESP, подвеска, 4MATIC)"; break;
    case 'B': d = "Кузов (SAM, свет, климат, подушки)"; break;
    case 'U': d = "Сеть обмена данными (CAN)"; break;
    default: d = "Неизвестная система";
  }
  if (code[0] == 'P' && (code[1] == '0' || code[1] == '1')) {
    const char *sub = P_SUBSYSTEM[code[2] - '0'];
    if (code[2] >= '0' && code[2] <= '9' && *sub) d += std::string("; ") + sub;
  }
  bool manuf = code[0] == 'P' ? (code[1] == '1' || code[1] == '3') : (code[1] == '1' || code[1] == '2');
  if (manuf) d += "; код производителя (Mercedes) — точная расшифровка в Xentry/DAS";
  return d;
}

// round(x, nd) как в Python: от точного двоичного значения, половины — к чётному.
inline double pyRound(double x, int nd) {
  char s[48];
  snprintf(s, sizeof s, "%.*f", nd, x);
  return strtod(s, nullptr);
}

// ---- PID службы 01 (january.PIDS) -------------------------------------------

struct Pid {
  int pid;
  const char *name, *label, *unit;
  double (*f)(const Bytes &d);
};

inline double trim100(int a) { return (a - 128) * 100.0 / 128; }

static const Pid PIDS[] = {
    {0x04, "LOAD", "Нагрузка", "%", [](const Bytes &d) { return d.at(0) * 100.0 / 255; }},
    {0x05, "ECT", "Температура ОЖ", "°C", [](const Bytes &d) { return d.at(0) - 40.0; }},
    {0x06, "STFT", "Кратковременная коррекция топлива", "%", [](const Bytes &d) { return trim100(d.at(0)); }},
    {0x07, "LTFT", "Долговременная коррекция топлива", "%", [](const Bytes &d) { return trim100(d.at(0)); }},
    {0x0B, "MAP", "Давление во впуске", "кПа", [](const Bytes &d) { return (double)d.at(0); }},
    {0x0C, "RPM", "Обороты", "об/мин", [](const Bytes &d) { return (d.at(0) * 256 + d.at(1)) / 4.0; }},
    {0x0D, "SPEED", "Скорость", "км/ч", [](const Bytes &d) { return (double)d.at(0); }},
    {0x0E, "TIMING", "Угол опережения зажигания", "°", [](const Bytes &d) { return d.at(0) / 2.0 - 64; }},
    {0x0F, "IAT", "Температура воздуха", "°C", [](const Bytes &d) { return d.at(0) - 40.0; }},
    {0x10, "MAF", "Расход воздуха (ДМРВ)", "кг/ч",
     [](const Bytes &d) { return (d.at(0) * 256 + d.at(1)) / 100.0 * 3.6; }},
    {0x11, "TPS", "Положение дросселя", "%", [](const Bytes &d) { return d.at(0) * 100.0 / 255; }},
    {0x14, "O2S1", "Лямбда-зонд 1 (до катализатора)", "В", [](const Bytes &d) { return d.at(0) / 200.0; }},
    {0x15, "O2S2", "Лямбда-зонд 2 (после катализатора)", "В", [](const Bytes &d) { return d.at(0) / 200.0; }},
    {0x1F, "RUNTIME", "Время работы с запуска", "с", [](const Bytes &d) { return d.at(0) * 256.0 + d.at(1); }},
    {0x42, "VOLT", "Напряжение бортсети (ЭБУ)", "В", [](const Bytes &d) { return (d.at(0) * 256 + d.at(1)) / 1000.0; }},
};

inline const Pid *pidByName(const std::string &n) {
  for (const Pid &p : PIDS)
    if (n == p.name) return &p;
  return nullptr;
}

using Sample = std::vector<std::pair<std::string, double>>;  // порядок как в PIDS

inline bool get(const Sample &s, const char *name, double &v) {
  for (auto &kv : s)
    if (kv.first == name) { v = kv.second; return true; }
  return false;
}

// ---- подсказки по смеси (january.check_mixture) -----------------------------

inline std::string fmt(const char *f, double a, double b = 0) {
  char s[512];
  snprintf(s, sizeof s, f, a, b);
  return s;
}

inline std::vector<std::string> checkMixture(const std::vector<Sample> &samples, const std::string &fuel) {
  std::vector<const Sample *> running;
  for (auto &s : samples) {
    double rpm = 0;
    get(s, "RPM", rpm);
    if (rpm > 400) running.push_back(&s);
  }
  if (running.empty())
    return {"Двигатель не работал во время записи: коррекции, лямбда и ДМРВ "
            "оцениваются только на заведённом прогретом моторе."};
  std::vector<std::string> out;
  auto values = [&](const char *n) {
    std::vector<double> v;
    double x;
    for (auto *s : running)
      if (get(*s, n, x)) v.push_back(x);
    return v;
  };
  auto avg = [&](const char *n, double &r) {
    auto v = values(n);
    if (v.empty()) return false;
    double sum = 0;
    for (double x : v) sum += x;
    r = sum / v.size();
    return true;
  };
  double ect = 0, ltft = 0, stft = 0;
  bool hasEct = avg("ECT", ect);
  if (hasEct) {
    if (ect <= -35 || ect >= 135)
      out.push_back(fmt("ОЖ %.0f °C — нереальное значение: обрыв или замыкание датчика температуры ОЖ.", ect));
    else if (ect < 70)
      out.push_back(fmt("ОЖ всего %.0f °C: блок считает мотор холодным и обогащает смесь. Если ", ect) +
                    "мотор на самом деле тёплый (стрелка в норме) — врёт датчик ОЖ; если холодный — термостат.");
  }
  if (avg("LTFT", ltft)) {
    double total = ltft + (avg("STFT", stft) ? stft : 0);
    if (total < -10)
      out.push_back(fmt("Коррекции %+.0f %% (долговременная %+.0f %%): блок сам убирает топливо, ", total, ltft) +
                    "значит лишнее топливо приходит помимо расчёта — подтекающие форсунки, завышенное "
                    "давление (регулятор), клапан адсорбера, бензин в масле, или ДМРВ завышает расход воздуха.");
    else if (total > 10)
      out.push_back(fmt("Коррекции %+.0f %%: смесь бедная — подсос воздуха, слабый насос/фильтр, ", total) +
                    "грязные форсунки или ДМРВ занижает.");
  }
  if (!fuel.empty() && fuel.find("разомкнутый") != std::string::npos && hasEct && ect >= 70)
    out.push_back("Прогретый мотор, но топливная система: " + fuel +
                  ". Лямбда-зонд не используется — проверить зонд, его подогрев и проводку.");
  auto o2 = values("O2S1");
  if (o2.size() >= 5) {
    double lo = o2[0], hi = o2[0];
    for (double x : o2) { lo = std::min(lo, x); hi = std::max(hi, x); }
    if (lo > 0.6)
      out.push_back(fmt("Лямбда-зонд всё время %.2f–%.2f В — смесь постоянно богатая.", lo, hi));
    else if (hi < 0.3)
      out.push_back(fmt("Лямбда-зонд всё время %.2f–%.2f В — смесь бедная или зонд мёртв.", lo, hi));
    else if (hi - lo < 0.4)
      out.push_back(fmt("Лямбда-зонд почти не переключается (%.2f–%.2f В) — ленивый зонд.", lo, hi));
  }
  std::vector<double> maf;
  for (auto *s : running) {
    double rpm = 0, tps = 0, m;
    get(*s, "RPM", rpm);
    get(*s, "TPS", tps);
    if (rpm < 1100 && tps < 3 && get(*s, "MAF", m)) maf.push_back(m);
  }
  if (!maf.empty()) {
    double m = 0;
    for (double x : maf) m += x;
    m /= maf.size();
    if (m > 14)
      out.push_back(fmt("На холостом ДМРВ показывает %.1f кг/ч (для 1,5–1,6 л обычно 7–12): датчик может ", m) +
                    "завышать расход, и блок льёт больше топлива.");
  }
  auto tps = values("TPS");
  if (!tps.empty()) {
    double lo = tps[0];
    for (double x : tps) lo = std::min(lo, x);
    if (lo > 5) out.push_back(fmt("Дроссель не ниже %.0f %% даже на холостом — проверить ДПДЗ и упор дросселя.", lo));
  }
  if (out.empty())
    out.push_back("По живым данным явных причин перелива не видно: проверить давление топлива "
                  "манометром и форсунки на пролив/подтекание.");
  return out;
}

// ---- связь с блоком (elm.py ElmLink для K-line + january.py) ------------------

struct Dtc {
  std::string code, description, status;
};

struct Info {
  int address = 0;
  std::string ecu, vin, ident, adapter, voltage, fuel, dtcError;
  std::vector<Dtc> dtcs;
  std::vector<std::string> notes;
};

class Ecu {
 public:
  explicit Ecu(Io &io) : io(io) {}

  std::string cmd(const std::string &c, unsigned waitMs = 3000) {
    std::string r = io.cmd(c, waitMs + 2000);
    std::string out;
    for (char ch : r)
      if (ch != '>' && (unsigned char)ch < 128) out += ch;
    return trim(out);
  }

  // ElmLink.__init__
  void begin() {
    version = cmd("ATZ", 3000);
    for (const char *c : {"ATE0", "ATL0", "ATS1", "ATH0"}) cmd(c);
    mode = "";
    target = -1;
  }

  bool klineConnect(int address, bool fast) {
    cmd("ATPC");
    mode = "kline";
    target = -1;
    cmd(fast ? "ATSP5" : "ATSP4");
    cmd("ATH1");
    cmd("ATKW0");
    cmd("ATST FF");
    cmd("ATSH81" + hex2(address) + "F1");
    cmd("ATWM81" + hex2(address) + "F13E");
    if (fast && noFastInit) return autoInit(address);
    if (!fast && noSlowInit) {
      cmd("ATIIA" + hex2(address));
      return autoInit(address);
    }
    std::string reply;
    if (fast) {
      reply = cmd("ATFI", 4000);
      if (trim(reply) == "?") {
        noFastInit = true;
        return autoInit(address);
      }
    } else {
      cmd("ATIIA" + hex2(address));
      reply = cmd("ATSI", 6000);
      if (trim(reply) == "?") {
        noSlowInit = true;
        return autoInit(address);
      }
    }
    std::string u = upper(reply);
    bool ok = u.find("OK") != std::string::npos && u.find("ERROR") == std::string::npos;
    if (ok) target = address;
    return ok;
  }

  bool autoInit(int address) {
    std::string reply = cmd("3E01", 6000);
    int src;
    try {
      src = klineSource(reply);
    } catch (DiagError &) {
      return false;
    }
    if (src != address) return false;
    target = address;
    return true;
  }

  Bytes request(int address, const Bytes &req) {
    if (!isReadOnly(req)) throw DiagError("Запрос " + hexBytes(req) + " может изменить данные и заблокирован");
    if (mode != "kline" || target != address) {
      if (!klineConnect(address, true) && !klineConnect(address, false))
        throw DiagError("Блок 0x" + hex2(address) + " не отвечает на K-line");
    }
    std::string h;
    for (int b : req) h += hex2(b);
    return finalPayload(parseKline(cmd(h)), req[0]);
  }

  Bytes ask(const Bytes &req) { return checkPositive(request(address, req), req[0]); }

  // january.connect / identify / read_dtcs / supported_pids / read_fuel_system
  bool open(Info &info) {
    begin();
    address = -1;
    for (int a : {0x10, 0x11}) {
      for (bool fast : {true, false}) {
        if (klineConnect(a, fast)) {
          address = a;
          break;
        }
      }
      if (address >= 0) break;
    }
    if (address < 0) return false;
    info = Info();
    info.address = address;
    identify(info);
    readDtcs(info);
    supportedPids();
    if (hasPid(0x03)) {
      try {
        Bytes p = ask({0x01, 0x03});
        info.fuel = p.size() > 2 ? fuelText(p[2]) : "";
      } catch (DiagError &) {
      }
    }
    if (pids.empty()) info.notes.push_back("Блок не отдаёт стандартные живые данные (вероятно, Евро-2 без лямбды).");
    size_t nl = version.find_last_of("\r\n");
    info.adapter = trim(nl == std::string::npos ? version : version.substr(nl + 1));
    info.voltage = cmd("ATRV");
    return true;
  }

  void identify(Info &info) {
    for (const char *r : {"1A80", "1A86", "1A87", "1A8A", "1A90", "1A91", "1A92", "1A94", "1A97", "1A98"}) {
      Bytes p;
      try {
        p = ask({0x1A, (int)strtol(r + 2, nullptr, 16)});
      } catch (DiagError &) {
        continue;
      }
      std::string text = extractText(p, 2);
      if (!text.empty() && info.ident.find(text) == std::string::npos)
        info.ident = info.ident.empty() ? text : info.ident + " | " + text;
    }
    std::string joined, compact;
    for (size_t i = 0; i < info.ident.size(); i++) {
      if (info.ident.compare(i, 3, " | ") == 0) {
        joined += ' ';
        i += 2;
      } else {
        joined += info.ident[i];
      }
    }
    for (char c : joined)
      if (c != ' ') compact += c;
    info.vin = findVin(compact);
    info.ecu = findEcuNumber(joined);
  }

  void readDtcs(Info &info) {
    std::string errors;
    for (const Bytes &req : {Bytes{0x18, 0x00, 0xFF, 0x00}, Bytes{0x18, 0x02, 0xFF, 0x00}}) {
      try {
        Bytes p = checkPositive(request(address, req), 0x18);
        for (size_t i = 2; i + 2 < p.size(); i += 3) {
          int hi = p[i], lo = p[i + 1], st = p[i + 2];
          if (hi == 0 && lo == 0) continue;
          std::string code = dtcCode(hi, lo);
          info.dtcs.push_back({code, dtcDescription(code), kwpStatus(st)});
        }
        info.dtcError.clear();
        return;
      } catch (DiagError &e) {
        errors += (errors.empty() ? "" : "; ") + hexBytes(req) + ": " + e.what();
      }
    }
    info.dtcError = errors;
  }

  void supportedPids() {
    pids.clear();
    for (int base = 0; base <= 0x40; base += 0x20) {
      Bytes p;
      try {
        p = ask({0x01, base});
      } catch (DiagError &) {
        break;
      }
      if (p.size() < 6) break;
      for (int i = 0; i < 32; i++)
        if (p[2 + i / 8] & (0x80 >> (i % 8))) pids.push_back(base + i + 1);
      if (pids.empty() || pids.back() != base + 0x20) break;
    }
  }

  bool hasPid(int pid) const {
    for (int p : pids)
      if (p == pid) return true;
    return false;
  }

  Sample sample() {
    Sample s;
    for (const Pid &d : PIDS) {
      if (!hasPid(d.pid)) continue;
      try {
        Bytes p = ask({0x01, d.pid});
        Bytes data(p.begin() + 2, p.end());
        s.push_back({d.name, pyRound(d.f(data), 2)});
      } catch (DiagError &) {
      } catch (std::out_of_range &) {
      }
    }
    return s;
  }

  void close() {
    try {
      if (mode == "kline") cmd("ATPC");
    } catch (...) {
    }
    mode = "";
  }

  static std::string fuelText(int v) {
    switch (v) {
      case 1: return "разомкнутый контур (не прогрет)";
      case 2: return "работа по лямбда-зонду";
      case 4: return "разомкнутый контур (нагрузка/торможение)";
      case 8: return "разомкнутый контур (неисправность)";
      case 16: return "по лямбда-зонду, но с неисправностью датчика";
    }
    return "0x" + hex2(v);
  }

  static bool vinChar(char c) {
    return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z' && c != 'I' && c != 'O' && c != 'Q');
  }

  static std::string findVin(const std::string &s) {
    for (size_t i = 0; i + 17 <= s.size(); i++) {
      size_t k = 0;
      while (k < 17 && vinChar(s[i + k])) k++;
      if (k == 17) return s.substr(i, 17);
    }
    return "";
  }

  // \d{4,5}-\d{7}-\d{2,3}, как re.search
  static std::string findEcuNumber(const std::string &s) {
    auto digits = [&](size_t at, size_t n) {
      if (at + n > s.size()) return false;
      for (size_t k = 0; k < n; k++)
        if (!isdigit((unsigned char)s[at + k])) return false;
      return true;
    };
    for (size_t i = 0; i < s.size(); i++) {
      for (size_t n1 : {5, 4}) {
        if (!digits(i, n1) || i + n1 >= s.size() || s[i + n1] != '-') continue;
        size_t j = i + n1 + 1;
        if (!digits(j, 7) || j + 7 >= s.size() || s[j + 7] != '-') continue;
        size_t k = j + 8;
        for (size_t n3 : {3, 2})
          if (digits(k, n3)) return s.substr(i, k + n3 - i);
      }
    }
    return "";
  }

  std::vector<int> pids;
  std::string version;

 private:
  Io &io;
  std::string mode;
  int target = -1, address = -1;
  bool noFastInit = false, noSlowInit = false;
};

// ---- фоновый опрос и состояние для страницы (live.py Poller) ----------------

inline std::string jsonStr(const std::string &s) {
  std::string o = "\"";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') o += '\\', o += (char)c;
    else if (c == '\n') o += "\\n";
    else if (c == '\r') o += "\\r";
    else if (c == '\t') o += "\\t";
    else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
    else o += (char)c;
  }
  return o + "\"";
}

inline std::string jsonNum(double v) {
  if (!std::isfinite(v)) return "null";
  char b[32];
  snprintf(b, sizeof b, "%.10g", v);
  return b;
}

class Poller {
 public:
  static const size_t HISTORY = 120;
  double staleAfter = 5.0, interval = 0.2;

  explicit Poller(Io &io) : io(io), ecu(io) {}

  // Один шаг: подключение (после паузы повтора) или замер. Без живых данных — переподключение,
  // как LadaProfile.sample() в Python.
  void step() {
    if (!connected) {
      if (io.now() < retryAt) return;
      setStatus("connecting", "подключение…");
      bool ok = false;
      std::string why = "блок не отвечает на K-line (адреса 0x10, 0x11)";
      try {
        ok = ecu.open(info);
      } catch (std::exception &e) {
        why = e.what();
      }
      if (!ok) {
        ecu.close();
        setStatus("lost", "нет связи: " + why + ". Повтор через " + fmt("%.0f", backoff) + " с");
        retryAt = io.now() + backoff;
        backoff = std::min(backoff * 2, 10.0);
        return;
      }
      connected = true;
      backoff = 1.0;
      setStatus("online", "на связи");
    }
    Sample s;
    std::string err;
    if (ecu.pids.empty()) {
      err = "нет живых данных";
    } else {
      try {
        s = ecu.sample();
        if (s.empty()) err = "блок перестал отвечать";
      } catch (std::exception &e) {
        err = e.what();
      }
    }
    if (!err.empty()) {
      ecu.close();
      connected = false;
      reconnects++;
      setStatus("lost", "связь потеряна: " + err);
      return;
    }
    add(s);
    if (interval > 0) io.sleep(interval);
  }

  std::string snapshot(bool withHistory = false) const {
    double now = io.now();
    double rpm = 0;
    auto it = values.find("RPM");
    if (it != values.end()) rpm = it->second.first;
    bool running = rpm > 400;
    std::string o = "{\"profile\":\"lada\",\"status\":" + jsonStr(status) + ",\"message\":" + jsonStr(message);
    o += ",\"info\":{\"title\":" + jsonStr(info.address ? "ВАЗ «Январь», K-line 0x" + hex2(info.address) : "") +
         ",\"ecu\":" + jsonStr(info.ecu) + ",\"vin\":" + jsonStr(info.vin) + ",\"ident\":" + jsonStr(info.ident) +
         ",\"adapter\":" + jsonStr(info.adapter) + ",\"voltage\":" + jsonStr(info.voltage) + ",\"dtcs\":[";
    for (size_t i = 0; i < info.dtcs.size(); i++) {
      auto &d = info.dtcs[i];
      o += std::string(i ? "," : "") + "{\"code\":" + jsonStr(d.code) + ",\"description\":" +
           jsonStr(d.description) + ",\"status\":" + jsonStr(d.status) + "}";
    }
    o += "],\"dtc_error\":" + jsonStr(info.dtcError) + ",\"notes\":[";
    for (size_t i = 0; i < info.notes.size(); i++) o += std::string(i ? "," : "") + jsonStr(info.notes[i]);
    o += "],\"fuel_system\":" + jsonStr(info.fuel) + ",\"log\":\"\"},\"values\":{";
    bool first = true;
    for (const Pid &p : PIDS) {  // порядок плиток — как в Python
      auto v = values.find(p.name);
      if (v == values.end()) continue;
      double val = v->second.first, age = now - v->second.second;
      bool bad = false;
      for (auto &n : NORMS)
        if (v->first == n.name && running && !(n.lo <= val && val <= n.hi)) bad = true;
      o += std::string(first ? "" : ",") + jsonStr(p.name) + ":{\"label\":" + jsonStr(p.label) +
           ",\"unit\":" + jsonStr(p.unit) + ",\"value\":" + jsonNum(pyRound(val, 3)) +
           ",\"age\":" + jsonNum(pyRound(age, 1)) + ",\"stale\":" + (age > staleAfter ? "true" : "false") +
           ",\"bad\":" + (bad ? "true" : "false") + "}";
      first = false;
    }
    o += "},\"hints\":[";
    for (size_t i = 0; i < hints.size(); i++) o += std::string(i ? "," : "") + jsonStr(hints[i]);
    o += "],\"samples\":" + std::to_string(samples) + ",\"reconnects\":" + std::to_string(reconnects) +
         ",\"csv\":\"\",\"version\":" + std::to_string(version) +
         ",\"default_tiles\":[\"ECT\",\"RPM\",\"AFR\",\"INJ_MS\",\"LTFT\",\"STFT\",\"INJ_CORR\",\"O2S1\",\"MAF\",\"TPS\","
         "\"TIMING\",\"FUEL_LH\"]";
    if (withHistory) {
      o += ",\"history\":[";
      for (size_t i = 0; i < history.size(); i++) {
        o += std::string(i ? "," : "") + "{\"t\":" + jsonNum(pyRound(history[i].first - now, 1));
        for (auto &kv : history[i].second) o += "," + jsonStr(kv.first) + ":" + jsonNum(kv.second);
        o += "}";
      }
      o += "]";
    }
    return o + "}";
  }

  unsigned long version = 0;
  std::string status = "connecting", message = "подключение…";
  Info info;
  std::vector<std::string> hints;
  unsigned long samples = 0, reconnects = 0;

 private:
  struct Norm {
    const char *name;
    double lo, hi;
  };
  static constexpr Norm NORMS[] = {{"ECT", 80, 105}, {"LTFT", -10, 10}, {"STFT", -10, 10}, {"VOLT", 13.2, 14.8}};

  void setStatus(const std::string &s, const std::string &m) {
    status = s;
    message = m;
    version++;
  }

  void add(const Sample &s) {
    double now = io.now();
    for (auto &kv : s) values[kv.first] = {kv.second, now};
    history.push_back({now, s});
    if (history.size() > HISTORY) history.pop_front();
    samples++;
    std::vector<Sample> last;
    size_t from = history.size() > 60 ? history.size() - 60 : 0;
    for (size_t i = from; i < history.size(); i++) last.push_back(history[i].second);
    hints = checkMixture(last, info.fuel);
    version++;
  }

  Io &io;
  Ecu ecu;
  bool connected = false;
  double backoff = 1.0, retryAt = 0;
  std::map<std::string, std::pair<double, double>> values;
  std::deque<std::pair<double, Sample>> history;
};


}  // namespace lada
