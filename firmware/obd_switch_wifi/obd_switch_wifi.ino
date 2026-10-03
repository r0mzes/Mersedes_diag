// Беспроводной переключатель линий OBD: ESP32-S3 + плата LC Technology «ESP8266 Relay X4» (12 В).
// Протокол тот же, что у firmware/obd_switch (Arduino), только по Wi-Fi: TCP-порт 3333.
// В vito_diag:  --switch socket://obdswitch.local:3333   (или socket://<IP>:3333)
//
// Реле на плате LC (схема «дерево», см. docs/AUTO_SWITCH.md):
//   реле 1 = R1: NC машина 7,  NO машина 8,  COM -> X
//   реле 2 = R2: NC машина 9,  NO машина 11, COM -> Y
//   реле 3 = R3: NC X,         NO Y,         COM -> адаптер 7
//   реле 4 = питание адаптера: COM машина 16 (через предохранитель), NC адаптер 16
// Без питания и после перезагрузки все реле отпущены: линия 7, адаптер запитан.
// Если связь с ноутбуком пропала, переключатель сам возвращается в это состояние.
//
// Команды (строка, '\n'):  ID, STATE, SEL 7|8|9|11, PWR ON|OFF, MEAS, RESET.
// CAN ALT не поддерживается: на плате всего 4 реле.

#include <WiFi.h>
#include <ESPmDNS.h>

// ---- Wi-Fi: точка доступа телефона (раздача интернета), к ней же подключён ноутбук ----
const char *WIFI_SSID = "PHONE_HOTSPOT";
const char *WIFI_PASS = "password";
// Если сеть не найдена за 15 с, переключатель сам раздаёт Wi-Fi (тогда у ноутбука не будет интернета).
const char *AP_SSID = "OBD-SWITCH";
const char *AP_PASS = "obd12345";
const uint16_t TCP_PORT = 3333;

// ---- управление платой реле ----
// UART: ESP32-S3 GPIO17 (TX) -> RX платы (контакт RX разъёма ESP-01 или боковой колодки).
// Кадр платы LC: A0 <номер реле 1..4> <01 вкл / 00 выкл> <сумма трёх байт>.
const int RELAY_TX = 17, RELAY_RX = 18;
const uint32_t RELAY_BAUD = 115200;

// ---- замер напряжений: делители 100 кОм / 18 кОм (18 В -> 2,75 В), входы ADC1 ----
const float DIVIDER = (100.0 + 18.0) / 18.0;
const uint8_t MEAS_OBD[] = {16, 7, 8, 9, 11, 6, 14};
const uint8_t MEAS_GPIO[] = {1, 2, 4, 5, 6, 7, 8};
const uint8_t MEAS_COUNT = sizeof(MEAS_OBD);

WiFiServer server(TCP_PORT);
WiFiClient client;
String rx;

int selLine = 7;
bool pwrOn = true;
bool rel[5] = {false, false, false, false, false};  // rel[1..4]

void relay(uint8_t n, bool on) {
  uint8_t frame[4] = {0xA0, n, (uint8_t)(on ? 1 : 0), 0};
  frame[3] = (uint8_t)(frame[0] + frame[1] + frame[2]);
  Serial1.write(frame, 4);
  Serial1.flush();
  rel[n] = on;
  delay(30);  // реле успевает переключиться до следующей команды
}

void allDefault() {
  for (uint8_t n = 1; n <= 4; n++) relay(n, false);
  selLine = 7;
  pwrOn = true;
}

// Сначала меняем неактивную ветку, потом R3, потом бывшую активную:
// на адаптер не попадает промежуточный «чужой» контакт.
bool selectLine(int line) {
  bool t1, t2, t3;
  switch (line) {
    case 7:  t1 = false; t2 = false; t3 = false; break;
    case 8:  t1 = true;  t2 = false; t3 = false; break;
    case 9:  t1 = false; t2 = false; t3 = true;  break;
    case 11: t1 = false; t2 = true;  t3 = true;  break;
    default: return false;
  }
  if (!rel[3]) relay(2, t2); else relay(1, t1);
  relay(3, t3);
  relay(1, t1);
  relay(2, t2);
  selLine = line;
  return true;
}

String measure() {
  String s = "MEAS";
  for (uint8_t i = 0; i < MEAS_COUNT; i++) {
    const int n = 200;
    long sum = 0;
    int lo = 100000, hi = 0;
    for (int k = 0; k < n; k++) {
      int mv = analogReadMilliVolts(MEAS_GPIO[i]);
      sum += mv;
      if (mv < lo) lo = mv;
      if (mv > hi) hi = mv;
    }
    float f = DIVIDER / 1000.0;
    s += " " + String(MEAS_OBD[i]) + ":" + String(sum * f / n, 2) + ":" + String(lo * f, 2) + ":" +
         String(hi * f, 2);
  }
  return s;
}

String state() {
  return String("STATE SEL ") + selLine + " CAN STD" + (pwrOn ? " PWR ON" : " PWR OFF");
}

String handle(String c) {
  c.trim();
  c.toUpperCase();
  if (c == "ID") return "OBDSW 1 WIFI";
  if (c == "STATE") return state();
  if (c == "MEAS") return measure();
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
  if (c == "CAN STD") return "OK CAN STD";
  if (c == "CAN ALT") return "ERR CAN ALT: no relay on this board";
  return "ERR unknown command";
}

void setup() {
  Serial.begin(115200);  // USB: отладка и те же команды по кабелю
  Serial1.begin(RELAY_BAUD, SERIAL_8N1, RELAY_RX, RELAY_TX);
  analogSetAttenuation(ADC_11db);
  delay(500);  // плата реле успевает стартовать
  allDefault();

  WiFi.mode(WIFI_STA);
  WiFi.setHostname("obdswitch");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(200);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Wi-Fi: "); Serial.println(WiFi.localIP());
  } else {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASS);
    Serial.print("AP " ); Serial.print(AP_SSID); Serial.print(": "); Serial.println(WiFi.softAPIP());
  }
  MDNS.begin("obdswitch");
  MDNS.addService("obdswitch", "tcp", TCP_PORT);
  server.begin();
  server.setNoDelay(true);
  Serial.println("OBDSW 1 WIFI");
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

String usbBuf;

void loop() {
  if (server.hasClient()) {
    if (client && client.connected()) server.available().stop();  // только один клиент
    else { client = server.available(); rx = ""; }
  }
  if (client) {
    if (client.connected()) {
      serveStream(client, rx);
    } else {
      client.stop();
      allDefault();  // ноутбук пропал: вернуть линию 7 и питание адаптера
    }
  }
  serveStream(Serial, usbBuf);
  delay(2);
}
