// Имитация машины для проверки ядра самодельного CAN-адаптера (firmware/esp32_can_adapter)
// без железа. Читает команды ELM327 из stdin, отвечает в stdout, а вместо шины CAN —
// два «блока»:
//   * АКПП (EGS): запрос 0x7E1 (и общий 0x7DF), ответ 0x7E9, KWP2000;
//   * скрытый блок: запрос 0x740, ответ 0x748 — отвечает только на свой ID;
// плюс посторонний кадр 0x240 на шине каждые 50 мс.
// Длинные ответы блоки досылают только после Flow Control, как настоящие.
//
// Сборка: g++ -std=c++17 -I firmware/esp32_can_adapter tests/can_sim.cpp -o can_sim

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#else
#include <poll.h>
#include <unistd.h>
#endif

#include <chrono>
#include <deque>
#include <string>
#include <thread>
#include <vector>

#include "elm_can_core.h"

using Bytes = std::vector<uint8_t>;

static uint32_t nowMs() {
  using namespace std::chrono;
  static auto start = steady_clock::now();
  return (uint32_t)duration_cast<milliseconds>(steady_clock::now() - start).count();
}

struct Pending {
  uint32_t at;
  CanFrame f;
};

struct Ecu {
  uint32_t rx, tx;
  bool functional;  // отвечает и на 0x7DF
  Bytes out;        // недосланный длинный ответ
  size_t pos = 0;
  uint8_t sn = 1;
  int fcCount = 0;
};

class Sim : public ElmCanIo {
 public:
  std::deque<Pending> queue;
  Ecu ecus[2] = {{0x7E1, 0x7E9, true, {}}, {0x740, 0x748, false, {}}};
  uint32_t nextNoise = 0;
  bool listenOnly = true;
  int sentWhileListening = 0;

  void write(const char *s) override {
    fputs(s, stdout);
    fflush(stdout);
  }

  bool canSend(const CanFrame &f) override {
    if (listenOnly) {
      sentWhileListening++;
      return false;
    }
    for (auto &e : ecus) {
      if (f.id == e.rx || (e.functional && f.id == 0x7DF)) onFrame(e, f);
    }
    return true;
  }

  bool canReceive(CanFrame &f, uint32_t timeoutMs) override {
    uint32_t end = nowMs() + timeoutMs;
    for (;;) {
      uint32_t t = nowMs();
      if ((int32_t)(t - nextNoise) >= 0) {
        nextNoise = t + 50;
        CanFrame n;
        n.id = 0x240;
        n.len = 8;
        n.data[0] = 0xA3;
        n.data[1] = 0x7E;
        queue.push_back({t, n});
      }
      for (auto it = queue.begin(); it != queue.end(); ++it) {
        if ((int32_t)(t - it->at) >= 0) {
          f = it->f;
          queue.erase(it);
          return true;
        }
      }
      if ((int32_t)(end - t) <= 0) return false;
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  }

  uint32_t now() override { return nowMs(); }

  bool inputPending() override {
#ifdef _WIN32
    // stdin — канал от тестов (subprocess); poll() на Windows нет
    DWORD avail = 0;
    return PeekNamedPipe(GetStdHandle(STD_INPUT_HANDLE), nullptr, 0, nullptr, &avail, nullptr) &&
           avail > 0;
#else
    pollfd p{0, POLLIN, 0};
    return poll(&p, 1, 0) > 0;
#endif
  }

  void discardInput() override {
    char c;
    while (inputPending() && read(0, &c, 1) == 1 && c != '\r') {
    }
  }

  void setListenOnly(bool on) override { listenOnly = on; }

  float batteryVolts() override { return 12.6f; }

 private:
  void push(const Ecu &e, const Bytes &data, uint32_t delay = 2) {
    CanFrame f;
    f.id = e.tx;
    f.len = 8;
    for (size_t i = 0; i < data.size() && i < 8; i++) f.data[i] = data[i];
    queue.push_back({nowMs() + delay, f});
  }

  void reply(Ecu &e, const Bytes &payload, uint32_t delay = 2) {
    if (payload.size() <= 7) {
      Bytes sf{(uint8_t)payload.size()};
      sf.insert(sf.end(), payload.begin(), payload.end());
      push(e, sf, delay);
      return;
    }
    Bytes ff{(uint8_t)(0x10 | payload.size() >> 8), (uint8_t)(payload.size() & 0xFF)};
    ff.insert(ff.end(), payload.begin(), payload.begin() + 6);
    push(e, ff, delay);
    e.out = payload;
    e.pos = 6;
    e.sn = 1;
  }

  void onFrame(Ecu &e, const CanFrame &f) {
    uint8_t pci = f.data[0] >> 4;
    if (pci == 3) {  // Flow Control от тестера
      if (e.out.empty()) return;
      e.fcCount++;
      int bs = f.data[1];
      int sent = 0;
      while (e.pos < e.out.size() && (bs == 0 || sent < bs)) {
        Bytes cf{(uint8_t)(0x20 | e.sn)};
        for (int i = 0; i < 7 && e.pos < e.out.size(); i++) cf.push_back(e.out[e.pos++]);
        push(e, cf, 2 + sent);
        e.sn = (e.sn + 1) & 0x0F;
        sent++;
      }
      if (e.pos >= e.out.size()) e.out.clear();
      return;
    }
    if (pci != 0) return;
    Bytes req(f.data + 1, f.data + 1 + (f.data[0] & 0x0F));
    if (req.empty()) return;
    e.out.clear();
    auto is = [&](Bytes b) { return req == b; };
    if (req[0] == 0x3E) return reply(e, {0x7E, (uint8_t)(req.size() > 1 ? req[1] : 0)});
    if (e.rx == 0x7E1) {
      if (is({0x1A, 0x86}))
        return reply(e, {0x5A, 0x86, 0x00, 0x34, 0x46, 0x41, 0x17, 0x07, 0x09, 0x08, 0x07, 0x09, 0x08,
                         0x03, 0x53, 0x00, 0x06, 0x05});
      if (is({0x1A, 0x87})) {
        reply(e, {0x7F, 0x1A, 0x78});  // «ответ будет позже»
        return reply(e, {0x5A, 0x87, 0x03, 0x08, 0x03, 0x53, 0x00, 0x09, 0x08, 0x07, 0x09, 0x08,
                         'A', '0', '0', '3', '4', '4', '6', '4', '1'}, 400);
      }
      if (req[0] == 0x18) return reply(e, {0x58, 0x02, 0x17, 0x31, 0x70, 0xD4, 0x0B, 0x60});
    } else {
      if (is({0x1A, 0x86})) {
        Bytes p{0x5A, 0x86};
        for (int i = 0; i < 60; i++) p.push_back((uint8_t)i);
        return reply(e, p);
      }
    }
    reply(e, {0x7F, req[0], 0x11});
  }
};

int main() {
#ifdef _WIN32
  // без этого Windows заменяет \n на \r\n в выводе
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  Sim sim;
  ElmCan elm(sim);
  std::string line;
  char c;
  while (read(0, &c, 1) == 1) {
    if (c == '\n') continue;
    if (c == '\r') {
      elm.handleLine(line.c_str());
      line.clear();
    } else {
      line += c;
    }
  }
  return sim.sentWhileListening ? 1 : 0;
}
