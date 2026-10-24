@echo off
chcp 65001 >nul
cd /d "%~dp0.."
where arduino-cli >nul 2>nul || (echo arduino-cli не найден. Установите: winget install ArduinoSA.CLI  или используйте Arduino IDE, см. docs\ESP32_BRIDGE.md & pause & exit /b 1)
arduino-cli config init >nul 2>nul
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32 || (pause & exit /b 1)
echo.
arduino-cli board list
set /p PORT="COM-порт ESP32 (например COM5): "
arduino-cli compile --upload -p %PORT% --fqbn "esp32:esp32:esp32:PartitionScheme=huge_app,UploadSpeed=230400" firmware\esp32_obd_bridge
pause
