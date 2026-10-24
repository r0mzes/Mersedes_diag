// Приборка без ноутбука: ESP32 раздаёт Wi-Fi «vito_diag», в телефоне открывается та же страница,
// что у python -m vito_diag web. K-line пока через ELM327 по Bluetooth (как esp32_obd_bridge),
// опрос «Января» — ядро lada_core.h (сверено с Python тестом tests/test_panel_firmware.py).
// Только чтение: ядро не отправляет запросов записи/стирания.
//
// Подключение: телефон -> Wi-Fi «vito_diag» (пароль vitodiag) -> http://192.168.4.1
// (многие телефоны сами откроют страницу как «вход в сеть»).
//
// USB (115200), служебные команды:
//   ~STATE              состояние Bluetooth, Wi-Fi и опроса
//   ~SCAN               поиск устройств Bluetooth (10 с)
//   ~USE <адрес>        адрес адаптера (aa:bb:cc:dd:ee:ff), запомнить и подключиться
//   ~PIN <pin>          PIN адаптера (по умолчанию 1234)
//   ~FORGET             забыть адрес адаптера
//   ~WIFI <имя> <пароль> имя и пароль точки доступа (пароль от 8 символов), применится после перезагрузки
//
// Сборка: arduino-cli compile --fqbn "esp32:esp32:esp32:PartitionScheme=huge_app" firmware/esp32_obd_panel
// После правки vito_diag/data/panel.html: python tools/gen_panel_assets.py

#include <BluetoothSerial.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_rom_sys.h>

#include "lada_core.h"
#include "panel_html.h"

const long USB_BAUD = 115200;
const int SCAN_MS = 10000;
const char *NAME_HINTS[] = {"OBD", "ELM", "V-LINK", "VLINK", "KONNWEI", "VGATE"};

BluetoothSerial bt;
Preferences prefs;
WebServer server(80);
DNSServer dns;
SemaphoreHandle_t btLock;    // Bluetooth: опрос или служебная команда, не оба сразу
SemaphoreHandle_t snapLock;  // готовые ответы для страницы
String addr, pin, ssid, pass;
String snapLive, snapFull;   // /api/stream и /api/state
unsigned long lastBtTry = 0;

// ---- ELM327 по Bluetooth для ядра --------------------------------------------

struct BtIo : lada::Io {
  std::string cmd(const std::string &c, unsigned waitMs) override {
    if (!bt.connected()) throw lada::DiagError("адаптер Bluetooth не подключён");
    while (bt.available()) bt.read();
    bt.print(c.c_str());
    bt.print('\r');
    std::string out;
    unsigned long until = millis() + waitMs;
    while ((long)(until - millis()) > 0) {
      while (bt.available()) {
        char ch = (char)bt.read();
        if (ch == '>') return out;
        out += ch;
      }
      if (!bt.connected()) throw lada::DiagError("адаптер Bluetooth отключился");
      delay(2);
    }
    return out;  // без '>' — как ElmLink: что успело прийти
  }
  double now() override { return millis() / 1000.0; }
  void sleep(double s) override { delay((uint32_t)(s * 1000)); }
};

BtIo io;
lada::Poller poller(io);

// ---- Bluetooth: поиск и подключение адаптера (как esp32_obd_bridge) ----------

bool looksLikeObd(String name) {
  name.toUpperCase();
  for (const char *h : NAME_HINTS)
    if (name.indexOf(h) >= 0) return true;
  return false;
}

String scan(bool autoPick) {
  Serial.println(F("~SCAN..."));
  BTScanResults *res = bt.discover(SCAN_MS);
  String found;
  if (!res) {
    Serial.println(F("~SCAN failed"));
    return found;
  }
  for (int i = 0; i < res->getCount(); i++) {
    BTAdvertisedDevice *d = res->getDevice(i);
    String a = d->getAddress().toString();
    String n = d->haveName() ? String(d->getName().c_str()) : String("?");
    Serial.printf("~  %s  %s\n", a.c_str(), n.c_str());
    if (autoPick && found.isEmpty() && looksLikeObd(n)) found = a;
  }
  Serial.printf("~SCAN done, %d found\n", res->getCount());
  return found;
}

bool connectAdapter() {
  if (addr.isEmpty()) {
    addr = scan(true);
    if (addr.isEmpty()) {
      Serial.println(F("~no OBD adapter found"));
      return false;
    }
    prefs.putString("addr", addr);
  }
  Serial.printf("~connecting %s\n", addr.c_str());
  bool ok = bt.connect(BTAddress(addr));
  Serial.println(ok ? F("~CONNECTED") : F("~connect failed"));
  return ok;
}

// ---- опрос в своей задаче ------------------------------------------------------

void publish() {
  String live = poller.snapshot(false).c_str();
  String full = poller.snapshot(true).c_str();
  xSemaphoreTake(snapLock, portMAX_DELAY);
  snapLive = live;
  snapFull = full;
  xSemaphoreGive(snapLock);
}

void pollTask(void *) {
  unsigned long lastPublish = 0;
  for (;;) {
    xSemaphoreTake(btLock, portMAX_DELAY);
    if (!bt.connected() && millis() - lastBtTry > 5000) {
      lastBtTry = millis();
      connectAdapter();
    }
    unsigned long before = poller.version;
    poller.step();
    xSemaphoreGive(btLock);
    // Новое состояние — сразу; без изменений — раз в секунду (обновить «возраст» значений).
    if (poller.version != before || millis() - lastPublish > 1000) {
      publish();
      lastPublish = millis();
    }
    delay(20);
  }
}

// ---- веб-сервер ------------------------------------------------------------------

String snap(bool full) {
  xSemaphoreTake(snapLock, portMAX_DELAY);
  String s = full ? snapFull : snapLive;
  xSemaphoreGive(snapLock);
  return s;
}

void setupWeb() {
  server.on("/", [] { server.send_P(200, "text/html; charset=utf-8", PANEL_HTML); });
  server.on("/api/state", [] {
    server.sendHeader("Cache-Control", "no-store");
    server.send(200, "application/json; charset=utf-8", snap(true));
  });
  // Синхронный сервер не держит долгие соединения: отдаём одно событие и закрываем,
  // EventSource сам переподключается через retry — получается опрос раз в ~0,7 с.
  server.on("/api/stream", [] {
    server.sendHeader("Cache-Control", "no-store");
    server.send(200, "text/event-stream; charset=utf-8", "retry: 700\ndata: " + snap(false) + "\n\n");
  });
  server.onNotFound([] {  // «вход в сеть» на телефоне -> приборка
    server.sendHeader("Location", "http://192.168.4.1/");
    server.send(302, "text/plain", "");
  });
  server.begin();
}

// ---- USB-команды ---------------------------------------------------------------

String line;

void report() {
  Serial.printf("~STATE bt=%d addr=%s pin=%s wifi=%s clients=%d poll=%s (%s) samples=%lu\n", bt.connected(),
                addr.isEmpty() ? "-" : addr.c_str(), pin.c_str(), ssid.c_str(), WiFi.softAPgetStationNum(),
                poller.status.c_str(), poller.message.c_str(), poller.samples);
}

void runCommand(String c) {
  c.trim();
  xSemaphoreTake(btLock, portMAX_DELAY);
  if (c == "STATE") {
    report();
  } else if (c == "SCAN") {
    scan(false);
  } else if (c.startsWith("USE ")) {
    addr = c.substring(4);
    addr.trim();
    addr.toLowerCase();
    prefs.putString("addr", addr);
    if (bt.connected()) bt.disconnect();
    connectAdapter();
  } else if (c.startsWith("PIN ")) {
    pin = c.substring(4);
    pin.trim();
    prefs.putString("pin", pin);
    bt.setPin(pin.c_str(), pin.length());
    report();
  } else if (c == "FORGET") {
    addr = "";
    prefs.remove("addr");
    report();
  } else if (c.startsWith("WIFI ")) {
    String rest = c.substring(5);
    int sp = rest.indexOf(' ');
    String s = sp > 0 ? rest.substring(0, sp) : rest, p = sp > 0 ? rest.substring(sp + 1) : "";
    if (p.length() < 8) {
      Serial.println(F("~пароль Wi-Fi — не короче 8 символов"));
    } else {
      prefs.putString("ssid", s);
      prefs.putString("pass", p);
      Serial.println(F("~сохранено, перезагрузите ESP32"));
    }
  } else {
    Serial.println("~? " + c);
  }
  xSemaphoreGive(btLock);
}

void setup() {
  Serial.begin(USB_BAUD);
  btLock = xSemaphoreCreateMutex();
  snapLock = xSemaphoreCreateMutex();
  prefs.begin("obd", false);
  addr = prefs.getString("addr", "");
  pin = prefs.getString("pin", "1234");
  ssid = prefs.getString("ssid", "vito_diag");
  pass = prefs.getString("pass", "vitodiag");

  WiFi.mode(WIFI_AP);
  WiFi.softAP(ssid.c_str(), pass.c_str());
  dns.start(53, "*", WiFi.softAPIP());
  setupWeb();

  bt.begin("vito_diag", true);
  bt.setPin(pin.c_str(), pin.length());
  esp_rom_install_channel_putc(1, nullptr);  // без «ASSERT_WARN» контроллера Bluetooth в USB
  publish();
  Serial.printf("~vito_diag panel: Wi-Fi %s, http://%s/\n", ssid.c_str(), WiFi.softAPIP().toString().c_str());
  xTaskCreatePinnedToCore(pollTask, "poll", 16384, nullptr, 1, nullptr, 1);
}

void loop() {
  dns.processNextRequest();
  server.handleClient();
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (line.startsWith("~")) runCommand(line.substring(1));
      line = "";
    } else {
      line += c;
    }
  }
  delay(2);
}
