@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
  start "" .venv\Scripts\pythonw.exe -m vito_diag gui
) else (
  echo Сначала запустите install_windows.bat
  pause
)
