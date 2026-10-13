// Самодельный CAN-адаптер для vito_diag: ESP32 (встроенный CAN-контроллер TWAI) + трансивер
// SN65HVD230. Компьютер видит COM-порт и говорит с ним командами ELM327, поэтому vito_diag
// работает без переделок: python -m vito_diag ecu scan-can --port COMx
//
// Схема и прошивка — docs/CAN_ADAPTER.md. Логика команд и ISO-TP — в elm_can_core.h.
//
// Безопасность: при включении и после ATPC/ATZ контроллер только слушает шину (не подтверждает
// чужие кадры и ничего не передаёт). Передача включается на время запроса. Какие запросы
// отправлять, решает vito_diag: он пропускает только чтение.

#include <driver/twai.h>

#include "elm_can_core.h"

// ---- настройки -------------------------------------------------------------
#if CONFIG_IDF_TARGET_ESP32S3
// ESP32-S3 (в т. ч. S3 CAM N16R8): свободные от камеры выводы.
const gpio_num_t CAN_TX = GPIO_NUM_1;   // -> CTX (TXD) трансивера
const gpio_num_t CAN_RX = GPIO_NUM_2;   // <- CRX (RXD) трансивера
#else
// Обычная ESP32 и ESP32-CAM (выводы карты памяти, карта не нужна).
const gpio_num_t CAN_TX = GPIO_NUM_14;  // -> CTX (TXD) трансивера
const gpio_num_t CAN_RX = GPIO_NUM_15;  // <- CRX (RXD) трансивера
#endif

const long PC_BAUD = 38400;  // как у моста ESP32: vito_diag по умолчанию открывает порт на 38400

// Напряжение АКБ для ATRV: вывод АЦП с делителем от контакта 16 OBD. -1 — не измеряется.
// Делитель 10 кОм (сверху) / 2,2 кОм (снизу): 16 В -> 2,9 В на входе.
const int VBAT_PIN = -1;
const float VBAT_DIVIDER = (10.0f + 2.2f) / 2.2f;

// -----------------------------------------------------------------------------

class Esp32Io : public ElmCanIo {
 public:
  bool listenOnly = true;
  bool installed = false;

  void begin() { install(true); }

  void write(const char *s) override { Serial.print(s); }

  // ATMA: если порт не успевает (38400 бод — около 120 кадров в секунду), ждём немного,
  // а потом сдаёмся с BUFFER FULL, как ELM327.
  bool canWrite(size_t n) override {
    uint32_t until = millis() + 50;
    while (Serial.availableForWrite() < (int)n) {
      if ((int32_t)(until - millis()) <= 0) return false;
      delay(1);
    }
    return true;
  }

  bool canSend(const CanFrame &f) override {
    if (!ensureRunning()) return false;
    twai_message_t m = {};
    m.identifier = f.id;
    m.extd = f.ext;
    m.data_length_code = f.len;
    memcpy(m.data, f.data, f.len);
    uint32_t alerts;
    twai_read_alerts(&alerts, 0);  // сбросить старые
    if (twai_transmit(&m, pdMS_TO_TICKS(50)) != ESP_OK) return false;
    // Ждём подтверждения (ACK) от других блоков. Если его нет (шина не подключена, перепутаны
    // CAN-H/CAN-L, не та скорость), контроллер повторял бы кадр бесконечно — вместо этого
    // сбрасываем передачу и возвращаемся в режим «только слушать».
    uint32_t until = millis() + 100;
    while ((int32_t)(until - millis()) > 0) {
      if (twai_read_alerts(&alerts, pdMS_TO_TICKS(10)) != ESP_OK) continue;
      if (alerts & TWAI_ALERT_TX_SUCCESS) return true;
      if (alerts & (TWAI_ALERT_TX_FAILED | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_OFF)) break;
    }
    install(true);
    return false;
  }

  bool canReceive(CanFrame &f, uint32_t timeoutMs) override {
    if (!ensureRunning()) {
      delay(timeoutMs);
      return false;
    }
    twai_message_t m;
    if (twai_receive(&m, pdMS_TO_TICKS(timeoutMs)) != ESP_OK) return false;
    if (m.rtr) return false;
    f.id = m.identifier;
    f.ext = m.extd;
    f.len = m.data_length_code > 8 ? 8 : m.data_length_code;
    memcpy(f.data, m.data, f.len);
    return true;
  }

  uint32_t now() override { return millis(); }

  bool inputPending() override { return Serial.available() > 0; }

  void discardInput() override {
    delay(5);
    while (Serial.available()) Serial.read();
  }

  void setListenOnly(bool on) override {
    if (on != listenOnly || !installed) install(on);
  }

  float batteryVolts() override {
    if (VBAT_PIN < 0) return -1;
    uint32_t mv = 0;
    for (int i = 0; i < 8; i++) mv += analogReadMilliVolts(VBAT_PIN);
    return mv / 8 / 1000.0f * VBAT_DIVIDER;
  }

 private:
  void install(bool listen) {
    if (installed) {
      twai_stop();
      twai_driver_uninstall();
      installed = false;
    }
    twai_general_config_t g =
        TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX, CAN_RX, listen ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL);
    g.rx_queue_len = 64;
    g.tx_queue_len = 8;
    g.alerts_enabled = TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_OFF;
    twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
    twai_filter_config_t fl = TWAI_FILTER_CONFIG_ACCEPT_ALL();
    if (twai_driver_install(&g, &t, &fl) == ESP_OK && twai_start() == ESP_OK) {
      installed = true;
      listenOnly = listen;
    }
  }

  // После ошибок шины (bus-off) контроллер сам не передаёт — перезапускаем драйвер.
  bool ensureRunning() {
    twai_status_info_t st;
    if (!installed || twai_get_status_info(&st) != ESP_OK) {
      install(listenOnly);
      return installed;
    }
    if (st.state == TWAI_STATE_BUS_OFF || st.state == TWAI_STATE_STOPPED) install(listenOnly);
    return installed;
  }
};

Esp32Io io;
ElmCan elm(io);
char lineBuf[128];
size_t lineLen = 0;

void setup() {
  Serial.begin(PC_BAUD);
  if (VBAT_PIN >= 0) analogSetPinAttenuation(VBAT_PIN, ADC_11db);
  io.begin();
  Serial.print("\r" ELM_CAN_VERSION "\r\r>");
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') continue;
    if (c == '\r') {
      lineBuf[lineLen] = 0;
      elm.handleLine(lineBuf);
      lineLen = 0;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    }
  }
}
