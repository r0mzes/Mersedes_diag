// Переключатель линий OBD по Bluetooth: ESP32-CAM (AI-Thinker) + 4 отдельных модуля реле на 5 В
// (JQC-3FF-S-Z / SRD-05VDC, вход IN). Протокол тот же, что у firmware/obd_switch_bt:
// в vito_diag  --switch COMx  (или через мост ~USE <адрес OBD-SWITCH>).
//
// Реле (схема «дерево», см. docs/WIFI_SWITCH.md):
//   реле 1 = R1: NC машина 7,  NO машина 8,  COM -> X           IN <- IO14
//   реле 2 = R2: NC машина 9,  NO машина 11, COM -> Y           IN <- IO15
//   реле 3 = R3: NC X,         NO Y,         COM -> адаптер 7   IN <- IO13
//   реле 4 = питание адаптера: COM машина 16 (через предохранитель), NC адаптер 16   IN <- IO2
// Питание: DC+ модулей и 5V ESP32-CAM от 5 В (в машине понижающий модуль 12->5 В), DC- и GND общие.
// Без питания, после перезагрузки и при разрыве Bluetooth все реле отпущены: линия 7, адаптер запитан.
//
// Свободных входов АЦП не остаётся, поэтому MEAS не поддерживается.
// SD-карту не вставлять. IO12 и IO4 не использовать (12 мешает загрузке, на 4 висит вспышка).
// Прошивать с отключёнными IN: модуль на IO2 может помешать входу в режим прошивки.
//
// Команды: ID, STATE, SEL 7|8|9|11, PWR ON|OFF, RESET.
// Отладка: REL n ON|OFF — одно реле напрямую (n = 1..4), минуя защиту «дерева».

#include <BluetoothSerial.h>

const char *BT_NAME = "OBD-SWITCH";

// Модуль включается высоким уровнем на IN (high level trigger) -> false.
// Включается низким уровнем -> true; но тогда 3,3 В ESP32 может не хватить, чтобы его выключить.
const bool RELAY_ACTIVE_LOW = false;
const uint8_t RELAY_PIN[5] = {0, 14, 15, 13, 2};  // индекс = номер реле

BluetoothSerial SerialBT;
String btBuf, usbBuf;
bool wasConnected = false;

int selLine = 7;
bool pwrOn = true;
bool rel[5] = {false, false, false, false, false};

void relay(uint8_t n, bool on) {
  digitalWrite(RELAY_PIN[n], (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
  rel[n] = on;
  delay(30);  // реле успевает переключиться до следующей команды
}

void allDefault() {
  for (uint8_t n = 1; n <= 4; n++) relay(n, false);
  selLine = 7;
  pwrOn = true;
}

bool selectLine(int line) {
  bool t1, t2, t3;
  switch (line) {
    case 7:  t1 = false; t2 = false; t3 = false; break;
    case 8:  t1 = true;  t2 = false; t3 = false; break;
    case 9:  t1 = false; t2 = false; t3 = true;  break;
    case 11: t1 = false; t2 = true;  t3 = true;  break;
    default: return false;
  }
  // сначала неактивная ветка, потом R3, потом бывшая активная
  if (!rel[3]) relay(2, t2); else relay(1, t1);
  relay(3, t3);
  relay(1, t1);
  relay(2, t2);
  selLine = line;
  return true;
}

String handle(String c) {
  c.trim();
  c.toUpperCase();
  if (c == "ID") return "OBDSW 1 BT";
  if (c == "STATE") return String("STATE SEL ") + selLine + " CAN STD" + (pwrOn ? " PWR ON" : " PWR OFF");
  if (c == "MEAS") return "ERR MEAS: no ADC inputs on this build";
  if (c == "RESET") { allDefault(); return "OK RESET"; }
  if (c.startsWith("SEL ")) {
    int line = c.substring(4).toInt();
    return selectLine(line) ? "OK SEL " + String(line) : "ERR SEL: only 7, 8, 9, 11";
  }
  if (c == "PWR ON" || c == "PWR OFF") {
    pwrOn = c.endsWith("ON");
    relay(4, !pwrOn);
    return pwrOn ? "OK PWR ON" : "OK PWR OFF";
  }
  if (c.startsWith("REL ") && (c.endsWith(" ON") || c.endsWith(" OFF"))) {
    int n = c.substring(4).toInt();
    if (n < 1 || n > 4) return "ERR REL: only 1..4";
    bool on = c.endsWith(" ON");
    relay(n, on);
    return "OK REL " + String(n) + (on ? " ON" : " OFF");
  }
  if (c == "CAN STD") return "OK CAN STD";
  if (c == "CAN ALT") return "ERR CAN ALT: no relay on this board";
  return "ERR unknown command";
}

void serveStream(Stream &io, String &buf) {
  while (io.available()) {
    char ch = io.read();
    if (ch == '\n' || ch == '\r') {
      if (buf.length()) io.println(handle(buf));
      buf = "";
    } else if (buf.length() < 32) {
      buf += ch;
    }
  }
}

void setup() {
  // уровень «выключено» выставляем до перевода вывода в выход, чтобы реле не дёрнулось
  for (uint8_t n = 1; n <= 4; n++) {
    digitalWrite(RELAY_PIN[n], RELAY_ACTIVE_LOW ? HIGH : LOW);
    pinMode(RELAY_PIN[n], OUTPUT);
  }
  allDefault();
  Serial.begin(115200);  // через плату ESP32-CAM-MB: отладка и те же команды
  SerialBT.begin(BT_NAME);
  Serial.println("OBDSW 1 BT");
}

void loop() {
  bool connected = SerialBT.hasClient();
  if (wasConnected && !connected) {
    allDefault();  // ноутбук пропал: вернуть линию 7 и питание адаптера
    btBuf = "";
  }
  wasConnected = connected;
  serveStream(SerialBT, btBuf);
  serveStream(Serial, usbBuf);
  delay(2);
}
