// Автоматический переключатель линий OBD для vito_diag (Arduino Uno / Nano).
//
// Реле R1..R3 собраны «деревом»: на контакт 7 адаптера физически может попасть
// только ОДИН контакт машины (7, 8, 9 или 11). Без питания и после сброса
// все реле отпущены: контакт 7 машины -> контакт 7 адаптера, как без переходника.
//
//   R1: NC = машина 7,  NO = машина 8,  COM -> X
//   R2: NC = машина 9,  NO = машина 11, COM -> Y
//   R3: NC = X,         NO = Y,         COM -> адаптер 7
//   R4, R5: CAN адаптера (6/14) -> NC = машина 6/14, NO = альтернативная пара (после замеров)
//   R6: +12 В адаптера (контакт 16) через NC. Реле включено = адаптер обесточен.
//
// Протокол: SERIAL_BAUD бод, команды строкой с '\n'. Ответ — одна строка.
//   ID               -> OBDSW 1
//   SEL 7|8|9|11     -> OK SEL 9
//   CAN STD|ALT      -> OK CAN ALT
//   PWR ON|OFF       -> OK PWR OFF      (питание адаптера ELM327)
//   STATE            -> STATE SEL 7 CAN STD PWR ON
//   MEAS             -> MEAS 16:12.41:12.38:12.45 7:... (контакт:среднее:мин:макс, вольты)
//   RESET            -> OK RESET        (всё в исходное состояние)
// Ошибка -> ERR <текст>.

// Скорость порта: 115200 — управление по USB-кабелю; 9600 — Bluetooth-модуль HC-06 на D0/D1
// (скорость HC-06 по умолчанию). На время прошивки по USB отключайте HC-06 от D0.
const long SERIAL_BAUD = 9600;  // наша сборка: HC-06

// HC-06 подключён «как к переходнику»: TXD модуля на D1, RXD на D0 (так удобно слать ему
// AT-команды с компьютера через USB Uno). Тогда связь идёт программным портом: приём D1,
// передача D0, а аппаратный UART не включается. Для такого подключения поставьте true.
const bool LINK_CROSSED = true;  // наша сборка: HC-06 TXD на D1

#include <SoftwareSerial.h>
SoftwareSerial crossed(1, 0);  // RX = D1, TX = D0
Stream *io = &Serial;


// ---- настройка под ваш модуль реле ----
const bool RELAY_ACTIVE_LOW = true;  // большинство модулей с оптронами включаются низким уровнем

const uint8_t PIN_R1 = 2, PIN_R2 = 3, PIN_R3 = 4;
const uint8_t PIN_CAN_H = 5, PIN_CAN_L = 6;
const uint8_t PIN_PWR = 7;
const uint8_t RELAYS[] = {PIN_R1, PIN_R2, PIN_R3, PIN_CAN_H, PIN_CAN_L, PIN_PWR};

// Делители 100 кОм / 33 кОм: 20 В на входе -> 4,96 В на АЦП.
const float DIVIDER = (100.0 + 33.0) / 33.0;
float VREF = 5.0;  // уточните мультиметром на выводе 5V и поправьте

// Контакты машины, которые меряем, и входы АЦП. A6/A7 есть только у Nano/Pro Mini.
const uint8_t MEAS_OBD[] = {16, 7, 8, 9, 11, 6, 14};
const uint8_t MEAS_ADC[] = {A0, A1, A2, A3, A4, A5, A6};
#if defined(ARDUINO_AVR_UNO)
const uint8_t MEAS_COUNT = 6;  // у Uno нет A6: контакт 14 не меряем
#else
const uint8_t MEAS_COUNT = 7;
#endif

int selLine = 7;
bool canAlt = false;
bool pwrOn = true;
bool r1 = false, r2 = false, r3 = false;

void relay(uint8_t pin, bool on) {
  digitalWrite(pin, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

void allDefault() {
  for (uint8_t p : RELAYS) relay(p, false);
  r1 = r2 = r3 = false;
  selLine = 7;
  canAlt = false;
  pwrOn = true;
}

// Переключение без промежуточного подключения «чужого» контакта:
// сначала меняем неактивную ветку, потом R3, потом бывшую активную.
bool selectLine(int line) {
  bool t1, t2, t3;
  switch (line) {
    case 7:  t1 = false; t2 = false; t3 = false; break;
    case 8:  t1 = true;  t2 = false; t3 = false; break;
    case 9:  t1 = false; t2 = false; t3 = true;  break;
    case 11: t1 = false; t2 = true;  t3 = true;  break;
    default: return false;
  }
  if (!r3) { relay(PIN_R2, t2); r2 = t2; }  // активна ветка X, Y свободна
  else     { relay(PIN_R1, t1); r1 = t1; }  // активна ветка Y, X свободна
  delay(20);
  relay(PIN_R3, t3); r3 = t3;
  delay(20);
  relay(PIN_R1, t1); r1 = t1;
  relay(PIN_R2, t2); r2 = t2;
  delay(20);
  selLine = line;
  return true;
}

void measure() {
  io->print(F("MEAS"));
  for (uint8_t i = 0; i < MEAS_COUNT; i++) {
    analogRead(MEAS_ADC[i]);  // первый отсчёт после смены канала отбрасываем
    long sum = 0;
    int lo = 1023, hi = 0;
    const int n = 200;  // ~25 мс на канал: видно, есть ли обмен на линии
    for (int k = 0; k < n; k++) {
      int v = analogRead(MEAS_ADC[i]);
      sum += v;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    float k = VREF / 1023.0 * DIVIDER;
    io->print(' ');
    io->print(MEAS_OBD[i]);
    io->print(':'); io->print(sum * k / n, 2);
    io->print(':'); io->print(lo * k, 2);
    io->print(':'); io->print(hi * k, 2);
  }
  io->println();
}

void printState() {
  io->print(F("STATE SEL ")); io->print(selLine);
  io->print(canAlt ? F(" CAN ALT") : F(" CAN STD"));
  io->println(pwrOn ? F(" PWR ON") : F(" PWR OFF"));
}

void handle(String c) {
  c.trim();
  c.toUpperCase();
  if (c.length() == 0) return;
  if (c == "ID") { io->println(F("OBDSW 1")); return; }
  if (c == "STATE") { printState(); return; }
  if (c == "MEAS") { measure(); return; }
  if (c == "RESET") { allDefault(); io->println(F("OK RESET")); return; }
  if (c.startsWith("SEL ")) {
    int line = c.substring(4).toInt();
    if (selectLine(line)) { io->print(F("OK SEL ")); io->println(line); }
    else io->println(F("ERR SEL: only 7, 8, 9, 11"));
    return;
  }
  if (c == "CAN STD" || c == "CAN ALT") {
    canAlt = c.endsWith("ALT");
    relay(PIN_CAN_H, canAlt);
    relay(PIN_CAN_L, canAlt);
    io->println(canAlt ? F("OK CAN ALT") : F("OK CAN STD"));
    return;
  }
  if (c == "PWR ON" || c == "PWR OFF") {
    pwrOn = c.endsWith("ON");
    relay(PIN_PWR, !pwrOn);
    io->println(pwrOn ? F("OK PWR ON") : F("OK PWR OFF"));
    return;
  }
  // показываем, что именно пришло (в hex): видно, если байты искажаются по дороге
  io->print(F("ERR unknown command:"));
  for (uint8_t i = 0; i < c.length(); i++) {
    io->print(' ');
    if ((uint8_t)c[i] < 16) io->print('0');
    io->print((uint8_t)c[i], HEX);
  }
  io->println();
}

void setup() {
  // Сначала уровень «выключено», потом OUTPUT — чтобы реле не щёлкнули при старте.
  for (uint8_t p : RELAYS) { relay(p, false); pinMode(p, OUTPUT); }
  allDefault();
  if (LINK_CROSSED) { crossed.begin(SERIAL_BAUD); io = &crossed; }
  else Serial.begin(SERIAL_BAUD);
  io->println(F("OBDSW 1"));
}

String buf;

void loop() {
  while (io->available()) {
    char ch = io->read();
    if (ch == '\n' || ch == '\r') {
      handle(buf);
      buf = "";
    } else if (ch >= 32 && ch < 127 && buf.length() < 32) {
      buf += ch;  // мусорные байты (например 0xFF от помехи на линии HC-06) отбрасываем
    }
  }
}
