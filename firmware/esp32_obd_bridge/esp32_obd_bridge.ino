// Мост USB(UART) <-> Bluetooth SPP для адаптера ELM327.
// ESP32 работает ведущим: сама находит адаптер, подключается по PIN и пересылает данные.
// Компьютер видит COM-порт FTDI на 38400.
//
// Служебные команды (строка начинается с '~', адаптеру не пересылается):
//   ~STATE        состояние связи и сохранённый адрес
//   ~SCAN         поиск устройств Bluetooth (10 с)
//   ~USE <адрес>  запомнить адрес адаптера (aa:bb:cc:dd:ee:ff) и подключиться
//   ~PIN <pin>    сменить PIN (по умолчанию 1234)
//   ~FORGET       забыть адрес, при следующем запуске искать заново

#include <BluetoothSerial.h>
#include <Preferences.h>
#include <esp_rom_sys.h>

const long PC_BAUD = 38400;
const int SCAN_MS = 10000;
const char *NAME_HINTS[] = {"OBD", "ELM", "V-LINK", "VLINK", "CAN", "KONNWEI", "VGATE"};

BluetoothSerial bt;
Preferences prefs;
String addr;
String pin;
bool wasConnected = false;
unsigned long lastTry = 0;

bool lineStart = true;
bool inCmd = false;
String cmd;

bool looksLikeObd(String name) {
  name.toUpperCase();
  for (const char *h : NAME_HINTS) {
    if (name.indexOf(h) >= 0) return true;
  }
  return false;
}

// Ищет устройства; если autoPick, возвращает адрес первого похожего на OBD.
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
    Serial.printf("~  %s  %s  rssi=%d\n", a.c_str(), n.c_str(), d->haveRSSI() ? d->getRSSI() : 0);
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
  Serial.printf("~connecting %s pin=%s\n", addr.c_str(), pin.c_str());
  bool ok = bt.connect(BTAddress(addr));
  Serial.println(ok ? F("~CONNECTED") : F("~connect failed"));
  return ok;
}

void report() {
  Serial.printf("~STATE connected=%d addr=%s pin=%s\n", bt.connected(), addr.isEmpty() ? "-" : addr.c_str(), pin.c_str());
}

void runCommand() {
  cmd.trim();
  if (cmd == "STATE") {
    report();
  } else if (cmd == "SCAN") {
    scan(false);
  } else if (cmd.startsWith("USE ")) {
    addr = cmd.substring(4);
    addr.trim();
    addr.toLowerCase();
    prefs.putString("addr", addr);
    if (bt.connected()) bt.disconnect();
    connectAdapter();
  } else if (cmd.startsWith("PIN ")) {
    pin = cmd.substring(4);
    pin.trim();
    prefs.putString("pin", pin);
    bt.setPin(pin.c_str(), pin.length());
    report();
  } else if (cmd == "FORGET") {
    addr = "";
    prefs.remove("addr");
    report();
  } else {
    Serial.println("~? " + cmd);
  }
}

void setup() {
  Serial.begin(PC_BAUD);
  prefs.begin("obd", false);
  addr = prefs.getString("addr", "");
  pin = prefs.getString("pin", "1234");

  bt.begin("ESP32-OBD", true);
  bt.setPin(pin.c_str(), pin.length());
  // Контроллер Bluetooth печатает в UART0 служебные "ASSERT_WARN ..." и ломает ответы ELM327.
  esp_rom_install_channel_putc(1, nullptr);
  Serial.println(F("~ESP32-OBD bridge ready"));
  wasConnected = connectAdapter();
  lastTry = millis();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (inCmd) {
      if (c == '\r' || c == '\n') {
        inCmd = false;
        lineStart = true;
        runCommand();
      } else {
        cmd += c;
      }
      continue;
    }
    if (lineStart && c == '~') {
      inCmd = true;
      cmd = "";
      continue;
    }
    lineStart = (c == '\r' || c == '\n');
    if (bt.connected()) bt.write(c);
  }

  while (bt.available()) {
    Serial.write(bt.read());
  }

  bool now = bt.connected();
  if (wasConnected && !now) Serial.println(F("~DISCONNECTED"));
  wasConnected = now;

  // Переподключение раз в 5 с, если адрес известен и связи нет.
  if (!now && !addr.isEmpty() && millis() - lastTry > 5000) {
    lastTry = millis();
    wasConnected = bt.connect();
    if (wasConnected) Serial.println(F("~CONNECTED"));
  }
}
