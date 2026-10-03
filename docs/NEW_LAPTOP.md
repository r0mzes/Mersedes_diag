# Перенос на другой ноутбук (Windows), по шагам

Всё нужное лежит в этом репозитории: программа `vito_diag`, прошивки
(`firmware/esp32_obd_bridge` — Bluetooth-мост, `firmware/obd_switch` — переключатель линий,
если он уже влит), скрипты и документация. Паролей и ключей в репозитории нет.

## 1. Программы (один раз)

Откройте PowerShell и выполните (или скачайте установщики с сайтов вручную):

```powershell
winget install Python.Python.3.12
winget install Git.Git
winget install ArduinoSA.IDE.stable      # Arduino IDE 2 — если будете прошивать платы
winget install ArduinoSA.CLI             # arduino-cli — для скрипта прошивки (по желанию)
```

Если ставите Python с python.org — отметьте **«Add Python to PATH»**.
После установки **закройте и заново откройте** PowerShell, чтобы появились `python` и `git`.

Проверка:
```powershell
python --version
git --version
```

## 2. Проект

```powershell
cd $HOME
git clone https://github.com/r0mzes/Mersedes_diag.git
cd Mersedes_diag
git checkout claude/mercedes-vito-diagnostics-1fgsio
.\install_windows.bat
```

`install_windows.bat` создаст окружение `.venv`, поставит библиотеки из `requirements.txt`
(`obd`, `pyserial`) и покажет демо-отчёт. Если демо-отчёт появился — программа работает.

Без git: на странице репозитория «Code → Download ZIP», распаковать, запустить
`install_windows.bat`.

Каждый раз перед работой в новом окне PowerShell:
```powershell
cd $HOME\Mersedes_diag
.\.venv\Scripts\Activate.ps1
```
(если PowerShell запрещает скрипты: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`,
или используйте `scan_windows.bat`, он всё делает сам).

## 3. Как ноутбук связывается с адаптером

Выберите свой вариант.

### А. Через мост ESP32 (так сделано сейчас)
USB-часть нашего адаптера неисправна, а у старого ноутбука нет Bluetooth, поэтому связь идёт
через ESP32 — см. [ESP32_BRIDGE.md](ESP32_BRIDGE.md).

1. Если ESP32 уже прошита — просто подключите её к ноутбуку через FTDI/USB.
   Драйвер FTDI обычно ставится сам; если в «Диспетчере устройств» неизвестное устройство —
   драйвер FTDI VCP (https://ftdichip.com/drivers/vcp-drivers/) или CH340 для плат с CH340.
2. Узнайте номер порта: «Диспетчер устройств → Порты (COM и LPT)» или
   `python -m vito_diag ports`. На старом ноутбуке это был **COM5**, на новом номер может быть другим.
3. Если плату нужно прошить заново — `tools\flash_esp32_bridge.bat` или Arduino IDE
   (плата ESP32 Dev Module, Partition Scheme «Huge APP», Upload Speed 230400).
4. Адаптер ищется сам (имя `OBD II`, PIN `1234`). Телефон к адаптеру не подключайте.

### Б. Bluetooth ноутбука напрямую
«Параметры → Bluetooth → Добавить устройство» → `OBD II`, PIN `1234`.
Затем «Дополнительные параметры Bluetooth → COM-порты» — нужен **исходящий** порт.

### В. USB-адаптер ELM327
Вставить в USB, найти COM-порт в «Диспетчере устройств», при необходимости — драйвер CH340/FTDI.

## 4. Проверка у машины

1. Адаптер в OBD-разъём (под панелью слева от рулевой колонки), **зажигание включить**.
2. Через мост дождитесь `~CONNECTED` (можно проверить командой из [ESP32_BRIDGE.md](ESP32_BRIDGE.md)).
3. Скан:
   ```powershell
   python -m vito_diag scan --port COM5 --baudrate 38400
   ```
   Замените `COM5` на свой порт. Отчёт — в папке `reports/`.

Остальные команды — в [README.md](../README.md), опрос всех блоков — `ecu scan-all`.

## 5. Claude Code на новом ноутбуке (по желанию)

См. [LAPTOP_CLAUDE_CODE.md](LAPTOP_CLAUDE_CODE.md). Коротко:
```powershell
irm https://claude.ai/install.ps1 | iex
cd $HOME\Mersedes_diag
claude
```
Claude прочитает `CLAUDE.md` с описанием машины и проекта. Скажите ему порт и способ связи,
например: «Мост ESP32 на COM5, 38400, зажигание включено. Сделай скан».

## 6. Прошивка переключателя линий (если собираете)

Схема и прошивка Arduino — в `docs/AUTO_SWITCH.md` и `firmware/obd_switch/` (появятся в
основной ветке после вливания PR про переключатель). Прошивается из Arduino IDE как обычный
скетч для Arduino Uno/Nano.

## 7. Работа над окном (GUI) на своём ПК

Машина для этого не нужна.

```powershell
cd $HOME\Mersedes_diag
git pull
copy samples\reports\* reports\     # реальные результаты 2026-09-30 (VIN скрыт)
.\gui_windows.bat                   # или: .venv\Scripts\python -m vito_diag gui
```

- Код окна — `vito_diag/gui.py` (Tkinter из стандартного Python, ставить ничего не надо).
  Окно запускает те же команды `python -m vito_diag ...` и показывает их вывод; разбор
  сохранённых результатов — функции `saved_results`, `describe_saved`, `format_saved`.
- Кнопки диагностики без машины проверяются с галочкой «Демо» (отчёты при этом не сохраняются).
- Тесты: `.venv\Scripts\python -m pytest` (окно — `tests/test_gui.py`).
- `reports/` и `logs/` в git не попадают (в них VIN и полный обмен с машиной, а репозиторий
  публичный). Чтобы поделиться результатом, скопируйте его в `samples/reports/`, скрыв VIN.
