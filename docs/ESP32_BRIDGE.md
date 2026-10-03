# Мост ESP32 ↔ Bluetooth-адаптер ELM327

Нужен, если у ноутбука нет Bluetooth (или он глючит), а адаптер ELM327 — Bluetooth.
ESP32 сама находит адаптер, подключается к нему по PIN и пересылает данные.
Ноутбук видит обычный COM-порт (через USB-UART) на скорости **38400**.

```
Vito (OBD) ── ELM327 «OBD II» ~~Bluetooth~~ ESP32 ── USB-UART (FTDI) ── ноутбук: vito_diag --port COMx --baudrate 38400
```

Скетч: [`firmware/esp32_obd_bridge/esp32_obd_bridge.ino`](../firmware/esp32_obd_bridge/esp32_obd_bridge.ino).

## Что проверено

| | |
|---|---|
| Плата | ESP32-D0WD-V3 (обычная ESP32, **не** S3/C3: у них нет классического Bluetooth SPP) |
| Адаптер | Bluetooth-имя `OBD II`, адрес `01:2d:a1:86:68:c0`, PIN `1234`, ответ `ELM327 v1.5` |
| Связь с ПК | FTDI, 38400 бод (на старом ПК это был COM5) |
| Не сработало | HC-06 (linvor V1.8) через Arduino Uno: ROLE=M принимает, но к адаптеру не подключается |

## Подключение ESP32 к USB-UART (FTDI)

| FTDI | ESP32 |
|---|---|
| GND | GND |
| TX | RX0 (GPIO3) |
| RX | TX0 (GPIO1) |
| 5V (или 3V3) | 5V (или 3V3) |

Если ESP32 на отладочной плате со своим USB — FTDI не нужен, достаточно кабеля USB.

Для прошивки ESP32 без кнопок нужны ещё DTR → GPIO0 и RTS → EN (или удерживать BOOT при
начале загрузки). Если провода DTR/RTS подключены, ESP32 может перезагружаться при
открытии порта программой: тогда подождите пару секунд до `~CONNECTED` и запустите
команду ещё раз.

## Прошивка

### Вариант 1: Arduino IDE
1. Установите Arduino IDE 2: https://www.arduino.cc/en/software
2. «Файл → Настройки → Дополнительные ссылки для Менеджера плат»:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
3. «Инструменты → Плата → Менеджер плат» → установить **esp32 by Espressif Systems**
   (проверено на версии 3.3.12). Отдельные библиотеки не нужны: `BluetoothSerial`
   и `Preferences` входят в пакет esp32.
4. Откройте `firmware/esp32_obd_bridge/esp32_obd_bridge.ino`.
5. «Инструменты»: плата **ESP32 Dev Module**, Partition Scheme **Huge APP (3MB No OTA)**,
   Upload Speed **230400**, порт — COM-порт вашего FTDI.
6. «Загрузить». Если зависает на `Connecting...` — зажмите BOOT (GPIO0 на землю), нажмите EN.

### Вариант 2: arduino-cli (скрипт)
Запустите [`tools/flash_esp32_bridge.bat`](../tools/flash_esp32_bridge.bat) и введите COM-порт.
Скрипт сам поставит ядро esp32 (нужен интернет) и прошьёт скетч.

Вручную:
```powershell
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32
arduino-cli compile --upload -p COM5 --fqbn "esp32:esp32:esp32:PartitionScheme=huge_app,UploadSpeed=230400" firmware\esp32_obd_bridge
```

## Первое подключение к адаптеру

1. Адаптер в OBD-разъём, зажигание включено (или адаптер на стенде 12 В).
   **Телефон к адаптеру в это время не подключать** — адаптер держит только одно соединение.
2. Подключите ESP32 к ноутбуку. При старте она сама ищет устройства с именами
   `OBD`, `ELM`, `V-LINK`, `VGATE` и т. п., подключается к первому найденному и запоминает
   его адрес (он сохраняется во флеше, после перепрошивки обычно остаётся).
3. Проверка из PowerShell (скрипт [`tools/esp_terminal.ps1`](../tools/esp_terminal.ps1)):
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\esp_terminal.ps1 -Port COM5 -Lines "~STATE","ATZ","ATI","AT RV"
   ```
   Ожидается `~STATE connected=1 ...`, затем `ELM327 v1.5` и напряжение (`12.xV`).
4. Если адаптер не найден сам — задайте адрес вручную:
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\esp_terminal.ps1 -Port COM5 -ListenMs 1000 -Lines "~USE 01:2d:a1:86:68:c0","wait:8000","~STATE"
   ```

### Служебные команды моста
Строка, начинающаяся с `~`, адаптеру не пересылается (заканчивать `\n`):

| Команда | Что делает |
|---|---|
| `~STATE` | Есть ли связь, сохранённый адрес и PIN |
| `~SCAN` | Поиск Bluetooth-устройств (10 с), печатает адреса и имена |
| `~USE aa:bb:cc:dd:ee:ff` | Запомнить адрес адаптера и подключиться |
| `~PIN 1234` | Сменить PIN (по умолчанию 1234) |
| `~FORGET` | Забыть адрес, при следующем запуске искать заново |

Сообщения моста тоже начинаются с `~` (`~CONNECTED`, `~DISCONNECTED`). При потере связи
мост переподключается каждые 5 секунд.

## Запуск vito_diag через мост

```powershell
python -m vito_diag scan --port COM5 --baudrate 38400
python -m vito_diag ecu scan-all --port COM5 --line 7
```
(номер COM — ваш, смотрите `python -m vito_diag ports` или «Диспетчер устройств → Порты».)

## Почему в скетче `esp_rom_install_channel_putc(1, nullptr)`

Контроллер Bluetooth ESP32 печатает служебные строки `ASSERT_WARN ... lc_task.c` в тот же
UART, через который идут ответы ELM327, и ломает их. Эта строка отключает такой вывод.
Не удаляйте её.

## Фото платы адаптера

В [`docs/photos/elm327_board/`](photos/elm327_board/) — фото платы этого адаптера (ELM327 v1.5
на PIC18F25K80 + FT232RQ). Сделаны при поиске неисправности: USB-часть адаптера не
работает (в Windows «Код 43», плохие провода к FT232RQ), поэтому связь идёт по Bluetooth
через мост.
