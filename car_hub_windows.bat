@echo off
chcp 65001 >nul
cd /d "%~dp0"
call .venv\Scripts\activate.bat

rem Опрос машины через общий блок obd_hub (docs/OBD_HUB.md):
rem   HUB — Arduino Uno по USB (CAN через MCP2515 + реле линий),
rem   BT  — мост ESP32 к ELM327 по Bluetooth (K-line).
rem Только чтение: ошибки не стираются, ничего не записывается.

set HUB=COM4
set BT=COM7

python -m vito_diag ports
echo.
set /p IN="Порт блока hub [%HUB%]: "
if not "%IN%"=="" set HUB=%IN%
set IN=
set /p IN="Порт моста ESP32 для K-line [%BT%]: "
if not "%IN%"=="" set BT=%IN%

:menu
echo.
echo ===== hub %HUB%, мост %BT% =====
echo  1  Замеры на контактах (сделать с выключенным и с включенным зажиганием)
echo  2  Послушать CAN 10 с (ничего не передаёт)
echo  3  Прочитать АКПП (CAN 7E1:7E9): идентификация и ошибки
echo  4  Найти блоки на CAN
echo  5  Найти блоки на K-line, линии 7, 9, 11 (через ELM327, 10-20 мин на линию)
echo  6  Всё сразу: CAN + K-line 7, 9, 11
echo  7  Проверить мост и ELM327 (~STATE, ATI, AT RV)
echo  0  Выход
set CH=
set /p CH="Выбор: "
if "%CH%"=="1" python -m vito_diag switch meas --switch %HUB%
if "%CH%"=="2" python -m vito_diag ecu monitor --hub %HUB% --seconds 10
if "%CH%"=="3" python -m vito_diag ecu read --hub %HUB% --can 7E1:7E9
if "%CH%"=="4" python -m vito_diag ecu scan-can --hub %HUB%
if "%CH%"=="5" python -m vito_diag ecu scan-kline --hub %HUB% --port %BT% --baudrate 38400 --line 7,9,11
if "%CH%"=="6" python -m vito_diag ecu scan-all --hub %HUB% --port %BT% --baudrate 38400 --line 7,9,11
if "%CH%"=="7" powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0tools\esp_terminal.ps1' -Port %BT% -Lines '~STATE','ATI','AT RV'"
if "%CH%"=="0" goto :eof
goto menu
