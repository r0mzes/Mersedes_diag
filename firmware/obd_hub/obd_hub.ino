// Общий блок диагностики для vito_diag (Arduino Uno / Nano, USB):
//   * CAN (OBD 6/14) через модуль MCP2515 + TJA1050 — с командами ELM327, которые клон не умеет:
//     свой ID запроса (ATSH), фильтр ответа (ATCRA), длинные ответы (Flow Control), ATMA;
//   * реле линий K-line: контакт 7 / 8 / 9 / 11 машины -> контакт 7 адаптера ELM327 («дерево»);
//   * реле питания адаптера ELM327 и замер напряжений на контактах разъёма.
// K-line сам блок не читает — это по-прежнему делает ELM327 через мост ESP32.
//
// Один COM-порт, 115200, две «грамматики» — по символу конца строки:
//   строка с '\r' — команда ELM327 (как шлёт vito_diag ElmLink), ответ заканчивается '>';
//   строка с '\n' — команда переключателя (как шлёт vito_diag LineSwitch), ответ — одна строка:
//     ID -> OBDSW 2 HUB      SEL 7|8|9|11 -> OK SEL 9      PWR ON|OFF -> OK PWR OFF
//     STATE -> STATE SEL 7 CAN STD PWR ON      MEAS -> MEAS 16:12.41:12.38:12.45 7:...
//     RESET -> OK RESET      ошибка -> ERR <текст>
//
// Схема — docs/OBD_HUB.md. Без питания все реле отпущены: контакт 7 -> контакт 7, адаптер запитан.
// При включении и после ATZ/ATPC MCP2515 только слушает шину (ничего не передаёт).
//
// Библиотека: «autowp-mcp2515» (Arduino IDE: Инструменты -> Управление библиотеками).

// Под 2 КБ ОЗУ Uno: один длинный ответ до 200 байт; STmin 1 мс — у MCP2515 всего два
// приёмных буфера, без паузы Uno может пропустить кадр.
#define ELM_MAX_PAYLOAD 200
#define ELM_MAX_SESSIONS 1
#define ELM_LINE_MAX 40
#define ELM_MIN_STMIN 1

#include <SPI.h>
#include <mcp2515.h>

#include "elm_can_core.h"

// ---- настройки ---------------------------------------------------------------
const long SERIAL_BAUD = 115200;

// Кварц на модуле MCP2515: надпись на металлическом корпусе X1 (8.000 -> 8 МГц, 16.000 -> 16 МГц).
const CAN_CLOCK MCP_CLOCK = MCP_8MHZ;
const uint8_t PIN_MCP_CS = 10;  // SCK D13, SI D11, SO D12; INT не нужен

const bool RELAY_ACTIVE_LOW = true;  // модули JQC-3FF-S-Z нашей сборки включаются низким уровнем
const uint8_t PIN_R1 = 2, PIN_R2 = 3, PIN_R3 = 4;
const uint8_t PIN_CAN_H = 5, PIN_CAN_L = 6;  // запас: вторая пара CAN для ELM (пока не используется)
const uint8_t PIN_PWR = 7;
const uint8_t RELAYS[] = {PIN_R1, PIN_R2, PIN_R3, PIN_CAN_H, PIN_CAN_L, PIN_PWR};

// Делители 10 кОм (сверху) / 2,2 кОм (снизу): 20 В на входе -> 3,6 В на АЦП.
const float DIVIDER = (10.0 + 2.2) / 2.2;
float VREF = 5.0;  // уточните мультиметром на выводе 5V и поправьте

const uint8_t MEAS_OBD[] = {16, 7, 8, 9, 11, 6, 14};
const uint8_t MEAS_ADC[] = {A0, A1, A2, A3, A4, A5, A6};
#if defined(ARDUINO_AVR_UNO)
const uint8_t MEAS_COUNT = 6;  // у Uno нет A6: контакт 14 не меряем
#else
const uint8_t MEAS_COUNT = 7;
#endif

// ---- CAN через MCP2515 -----------------------------------------------------------

MCP2515 mcp(PIN_MCP_CS);

float adcVolts(uint8_t pin) {
  analogRead(pin);
  long sum = 0;
  for (int i = 0; i < 16; i++) sum += analogRead(pin);
  return sum / 16.0 * VREF / 1023.0 * DIVIDER;
}

class HubIo : public ElmCanIo {
 public:
  bool listenOnly = true;

  void begin() {
    SPI.begin();
    restart(true);
  }

  void write(const char *s) override { Serial.print(s); }

  bool canWrite(size_t n) override {
    unsigned long until = millis() + 50;
    while (Serial.availableForWrite() < (int)n) {
      if ((long)(until - millis()) <= 0) return false;
    }
    return true;
  }

  bool canSend(const CanFrame &f) override {
    struct can_frame m;
    m.can_id = f.id;
    m.can_dlc = f.len;
    memcpy(m.data, f.data, f.len);
    mcp.clearTXInterrupts();
    if (mcp.sendMessage(MCP2515::TXB0, &m) != MCP2515::ERROR_OK) return false;
    // Ждём подтверждения (ACK). Нет его — шина не подключена, перепутаны CAN-H/CAN-L или не та
    // скорость: MCP2515 повторял бы кадр бесконечно. Сбрасываем и возвращаемся к прослушиванию.
    unsigned long until = millis() + 100;
    while ((long)(until - millis()) > 0) {
      if (mcp.getInterrupts() & MCP2515::CANINTF_TX0IF) return true;
    }
    restart(true);
    return false;
  }

  bool canReceive(CanFrame &f, uint32_t timeoutMs) override {
    unsigned long until = millis() + timeoutMs;
    struct can_frame m;
    for (;;) {
      if (mcp.readMessage(&m) == MCP2515::ERROR_OK) {
        if (m.can_id & CAN_RTR_FLAG) continue;
        f.ext = m.can_id & CAN_EFF_FLAG;
        f.id = m.can_id & (f.ext ? CAN_EFF_MASK : CAN_SFF_MASK);
        f.len = m.can_dlc > 8 ? 8 : m.can_dlc;
        memcpy(f.data, m.data, f.len);
        return true;
      }
      if ((long)(until - millis()) <= 0) return false;
    }
  }

  uint32_t now() override { return millis(); }

  bool inputPending() override { return Serial.available() > 0; }

  void discardInput() override {
    delay(5);
    while (Serial.available()) Serial.read();
  }

  void setListenOnly(bool on) override {
    if (on != listenOnly) restart(on);
  }

  float batteryVolts() override { return adcVolts(MEAS_ADC[0]); }  // контакт 16

 private:
  void restart(bool listen) {
    mcp.reset();  // заодно снимает неотправленные кадры
    mcp.setBitrate(CAN_500KBPS, MCP_CLOCK);
    if (listen) {
      mcp.setListenOnlyMode();
    } else {
      mcp.setNormalMode();
    }
    listenOnly = listen;
  }
};

HubIo io;
ElmCan elm(io);

// ---- реле и замеры (как firmware/obd_switch) ---------------------------------------

int selLine = 7;
bool canAlt = false;
bool pwrOn = true;
bool r1 = false, r2 = false, r3 = false;

void relay(uint8_t pin, bool on) { digitalWrite(pin, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW); }

void allDefault() {
  for (uint8_t p : RELAYS) relay(p, false);
  r1 = r2 = r3 = false;
  selLine = 7;
  canAlt = false;
  pwrOn = true;
}

// Переключение без промежуточного подключения «чужого» контакта:
// сначала неактивная ветка, потом R3, потом бывшая активная.
bool selectLine(int line) {
  bool t1, t2, t3;
  switch (line) {
    case 7:  t1 = false; t2 = false; t3 = false; break;
    case 8:  t1 = true;  t2 = false; t3 = false; break;
    case 9:  t1 = false; t2 = false; t3 = true;  break;
    case 11: t1 = false; t2 = true;  t3 = true;  break;
    default: return false;
  }
  if (!r3) { relay(PIN_R2, t2); r2 = t2; }
  else     { relay(PIN_R1, t1); r1 = t1; }
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
  Serial.print(F("MEAS"));
  for (uint8_t i = 0; i < MEAS_COUNT; i++) {
    analogRead(MEAS_ADC[i]);
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
    Serial.print(' ');
    Serial.print(MEAS_OBD[i]);
    Serial.print(':'); Serial.print(sum * k / n, 2);
    Serial.print(':'); Serial.print(lo * k, 2);
    Serial.print(':'); Serial.print(hi * k, 2);
  }
  Serial.println();
}

void printState() {
  Serial.print(F("STATE SEL ")); Serial.print(selLine);
  Serial.print(canAlt ? F(" CAN ALT") : F(" CAN STD"));
  Serial.println(pwrOn ? F(" PWR ON") : F(" PWR OFF"));
}

bool is(const char *c, const char *p) { return strcmp_P(c, p) == 0; }

void handleSwitch(char *c) {
  for (char *p = c; *p; p++) *p = toupper(*p);
  while (*c == ' ' || *c == '~') c++;  // '~' — как у моста ESP32, тоже можно
  if (is(c, PSTR("ID"))) { Serial.println(F("OBDSW 2 HUB")); return; }
  if (is(c, PSTR("STATE"))) { printState(); return; }
  if (is(c, PSTR("MEAS"))) { measure(); return; }
  if (is(c, PSTR("RESET"))) { allDefault(); Serial.println(F("OK RESET")); return; }
  if (strncmp_P(c, PSTR("SEL "), 4) == 0) {
    int line = atoi(c + 4);
    if (selectLine(line)) { Serial.print(F("OK SEL ")); Serial.println(line); }
    else Serial.println(F("ERR SEL: only 7, 8, 9, 11"));
    return;
  }
  if (is(c, PSTR("CAN STD")) || is(c, PSTR("CAN ALT"))) {
    canAlt = c[4] == 'A';
    relay(PIN_CAN_H, canAlt);
    relay(PIN_CAN_L, canAlt);
    Serial.println(canAlt ? F("OK CAN ALT") : F("OK CAN STD"));
    return;
  }
  if (is(c, PSTR("PWR ON")) || is(c, PSTR("PWR OFF"))) {
    pwrOn = c[5] == 'N';
    relay(PIN_PWR, !pwrOn);
    Serial.println(pwrOn ? F("OK PWR ON") : F("OK PWR OFF"));
    return;
  }
  Serial.print(F("ERR unknown command: "));
  Serial.println(c);
}

// ---- главный цикл ------------------------------------------------------------------

char lineBuf[ELM_LINE_MAX];
uint8_t lineLen = 0;

void setup() {
  for (uint8_t p : RELAYS) {
    relay(p, false);  // сначала уровень «выключено», потом выход — реле не щёлкнут при старте
    pinMode(p, OUTPUT);
  }
  allDefault();
  Serial.begin(SERIAL_BAUD);
  io.begin();
  Serial.print(F("\r" ELM_CAN_VERSION "\r\r>"));
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r' || c == '\n') {
      lineBuf[lineLen] = 0;
      if (c == '\r') {
        elm.handleLine(lineBuf);  // пустая строка у ELM327 — повтор команды
      } else if (lineLen) {
        handleSwitch(lineBuf);    // '\n' сразу после '\r' (CRLF) игнорируется
      }
      lineLen = 0;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    }
  }
}
