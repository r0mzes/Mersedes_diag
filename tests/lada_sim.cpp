// Ядро приборки (firmware/esp32_obd_panel/lada_core.h) на ПК: команды ELM327 уходят в stdout
// строкой "CMD <команда>", ответ приходит в stdin строкой hex-байтов (его даёт Python-эмулятор
// «Января»). После N шагов опроса печатает "SNAP <json>" — тест сверяет его с Python-версией.
//
// Сборка: g++ -std=c++17 -I firmware/esp32_obd_panel tests/lada_sim.cpp -o lada_sim
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>

#include "lada_core.h"

struct PipeIo : lada::Io {
  double t = 1000.0;

  std::string cmd(const std::string &c, unsigned) override {
    std::cout << "CMD " << c << "\n" << std::flush;
    std::string line;
    if (!std::getline(std::cin, line)) std::exit(3);
    std::string out;
    for (size_t i = 0; i + 1 < line.size(); i += 2) out += (char)std::strtol(line.substr(i, 2).c_str(), nullptr, 16);
    return out;
  }
  double now() override { return t; }
  void sleep(double s) override { t += s; }
};

int main(int argc, char **argv) {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  int steps = argc > 1 ? std::atoi(argv[1]) : 3;
  PipeIo io;
  lada::Poller poller(io);
  for (int i = 0; i < steps; i++) poller.step();
  std::cout << "SNAP " << poller.snapshot(true) << "\n" << std::flush;
  return 0;
}
