#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo
echo "Готово. Проверка в демо-режиме:"
python -m vito_diag scan --demo --no-save
echo
echo "Linux: чтобы работать с USB-адаптером без sudo — sudo usermod -aG dialout \$USER (и перелогиньтесь)."
