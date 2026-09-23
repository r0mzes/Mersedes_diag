@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Создание виртуального окружения Python...
python -m venv .venv || (echo Python не найден. Установите с https://www.python.org/downloads/ и отметьте "Add Python to PATH". & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Готово. Проверка в демо-режиме:
python -m vito_diag scan --demo --no-save
pause
