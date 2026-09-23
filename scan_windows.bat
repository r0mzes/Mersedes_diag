@echo off
chcp 65001 >nul
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m vito_diag ports
echo.
set /p PORT="Введите порт адаптера (например COM3) или Enter для автопоиска: "
if "%PORT%"=="" (
  python -m vito_diag scan
) else (
  python -m vito_diag scan --port %PORT%
)
pause
