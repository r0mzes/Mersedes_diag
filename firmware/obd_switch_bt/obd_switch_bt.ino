// Переключатель линий OBD по Bluetooth: ESP32-CAM (AI-Thinker, ESP32-S) + плата LC «ESP8266 Relay X4» (12 В).
// Ноутбук сопрягается с «OBD-SWITCH», Windows создаёт COM-порт. Протокол тот же, что у
// firmware/obd_switch: в vito_diag  --switch COMx  (номер порта — исходящий COM этого устройства).
//
// Реле на плате LC (схема «дерево», см. docs/WIFI_SWITCH.md):
//   реле 1 = R1: NC машина 7,  NO машина 8,  COM -> X
//   реле 2 = R2: NC машина 9,  NO машина 11, COM -> Y
//   реле 3 = R3: NC X,         NO Y,         COM -> адаптер 7
//   реле 4 = питание адаптера: COM машина 16 (через предохранитель), NC адаптер 16
// Без питания, после перезагрузки и при разрыве Bluetooth все реле отпущены: линия 7, адаптер запитан.
//
// Выводы ESP32-CAM (SD-карту не вставлять, камеру можно не снимать):
//   GPIO14 -> RX платы реле (UART 115200). UART0 (U0T) не берём: туда идут логи загрузки.
//   GPIO15 <- TX платы реле: контроллер платы шлёт AT-команды «модулю ESP-01», мы отвечаем.
//   GPIO2, GPIO13 <- делители 100к/18к от контактов машины 8, 9.
//   GPIO12 и GPIO4 не использовать (12 мешает загрузке, на 4 висит вспышка).
//
// Команды: ID, STATE, SEL 7|8|9|11, PWR ON|OFF, MEAS, RESET.
// Отладка платы реле: BOARD (что плата прислала), BAUD 9600|115200, IPD ON|OFF (кадры в обёртке +IPD).

#include <BluetoothSerial.h>

const char *BT_NAME = "OBD-SWITCH";

const int RELAY_TX = 14, RELAY_RX = 15;
uint32_t relayBaud = 115200;
bool ipdWrap = false;  // слать кадр как «+IPD,0,4:<кадр>», будто он пришёл по Wi-Fi
String boardLog, boardLine;

const float DIVIDER = (100.0 + 18.0) / 18.0;
const uint8_t MEAS_OBD[] = {8, 9};
const uint8_t MEAS_GPIO[] = {2, 13};  // ADC2: работает, пока Wi-Fi выключен
const uint8_t MEAS_COUNT = sizeof(MEAS_OBD);

BluetoothSerial SerialBT;
String btBuf, usbBuf;
bool wasConnected = false;

int selLine = 7;
bool pwrOn = true;
bool rel[5] = {false, false, false, false, false};

void relay(uint8_t n, bool on) {
  uint8_t frame[4] = {0xA0, n, (uint8_t)(on ? 1 : 0), 0};
  frame[3] = (uint8_t)(frame[0] + frame[1] + frame[2]);
  if (ipdWrap) Serial1.print("\r\n+IPD,0,4:");
  Serial1.write(frame, 4);
  Serial1.flush();
  rel[n] = on;
  delay(30);
}

// Контроллер платы LC (STM8/N76) рассчитан на ESP-01 с AT-прошивкой и принимает кадры реле
// только после того, как «ESP-01» сообщит о подключении к Wi-Fi. Изображаем эти строки
// (так же делают с этой платой в Tasmota).
void relayBoardInit() {
  Serial1.print("WIFI CONNECTED\r\nWIFI GOT IP\r\nAT+CIPMUX=1\r\nAT+CIPSERVER=1,8080\r\nAT+CIPSTO=360\r\n");
  Serial1.flush();
  delay(200);
}

// Ответы «модуля ESP-01» на AT-команды контроллера платы реле.
void answerBoard(const String &line) {
  if (!line.startsWith("AT")) return;
  if (line.startsWith("AT+RST")) {
    Serial1.print("\r\nOK\r\n");
    delay(100);
    Serial1.print("\r\nready\r\nWIFI CONNECTED\r\nWIFI GOT IP\r\n");
  } else if (line.startsWith("AT+CIFSR")) {
    Serial1.print("\r\n+CIFSR:APIP,\"192.168.4.1\"\r\n+CIFSR:STAIP,\"192.168.4.1\"\r\n\r\nOK\r\n");
  } else if (line.startsWith("AT+CIPSTATUS")) {
    Serial1.print("\r\nSTATUS:2\r\n\r\nOK\r\n");
  } else {
    Serial1.print("\r\nOK\r\n");
  }
  Serial1.flush();
}

void pollBoard() {
  while (Serial1.available()) {
    char ch = Serial1.read();
    if (boardLog.length() < 600) boardLog += (ch >= 32 && ch < 127) ? ch : (ch == '\n' ? '|' : (ch == '\r' ? ' ' : '?'));
    if (ch == '\n' || ch == '\r') {
      boardLine.trim();
      if (boardLine.length()) answerBoard(boardLine);
      boardLine = "";
    } else if (boardLine.length() < 64) {
      boardLine += ch;
    }
  }
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

String handle(String c) {
  c.trim();
  c.toUpperCase();
  if (c == "ID") return "OBDSW 1 BT";
  if (c == "STATE") return String("STATE SEL ") + selLine + " CAN STD" + (pwrOn ? " PWR ON" : " PWR OFF");
  if (c == "MEAS") return measure();
  if (c == "RESET") { relayBoardInit(); allDefault(); return "OK RESET"; }
  if (c.startsWith("SEL ")) {
    pollBoard();
    int line = c.substring(4).toInt();
    return selectLine(line) ? "OK SEL " + String(line) : "ERR SEL: only 7, 8, 9, 11";
  }
  if (c == "PWR ON" || c == "PWR OFF") {
    pwrOn = c.endsWith("ON");
    relay(4, !pwrOn);
    return pwrOn ? "OK PWR ON" : "OK PWR OFF";
  }
  if (c == "BOARD") { String r = "BOARD " + boardLog; boardLog = ""; return r; }
  if (c == "BAUD 9600" || c == "BAUD 115200") {
    relayBaud = c.substring(5).toInt();
    Serial1.updateBaudRate(relayBaud);
    return "OK BAUD " + String(relayBaud);
  }
  if (c == "IPD ON" || c == "IPD OFF") { ipdWrap = c.endsWith("ON"); return ipdWrap ? "OK IPD ON" : "OK IPD OFF"; }
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
  Serial.begin(115200);  // через плату ESP32-CAM-MB: отладка и те же команды
  Serial1.begin(relayBaud, SERIAL_8N1, RELAY_RX, RELAY_TX);
  analogSetAttenuation(ADC_11db);
  // Плата реле стартует и шлёт AT-команды: 2 с отвечаем на них, потом сообщаем «Wi-Fi подключён».
  for (uint32_t t0 = millis(); millis() - t0 < 2000;) { pollBoard(); delay(5); }
  relayBoardInit();
  allDefault();
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
  pollBoard();
  serveStream(SerialBT, btBuf);
  serveStream(Serial, usbBuf);
  delay(2);
}
