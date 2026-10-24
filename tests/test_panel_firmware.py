"""Прошивка приборки (firmware/esp32_obd_panel): ядро на C++ против Python-версии на одном эмуляторе.

Одинаковыми должны быть и команды адаптеру (по одной), и итог: блок, ошибки, значения, подсказки.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vito_diag.emulator import JanuaryEmulator
from vito_diag.live import LadaProfile, Poller

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware" / "esp32_obd_panel"


def test_generated_files_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_panel_assets.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    if shutil.which("g++") is None:
        pytest.skip("нужен g++")
    out = tmp_path_factory.mktemp("lada_sim") / "lada_sim"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Werror", "-O1", "-I", str(FW),
                    str(ROOT / "tests" / "lada_sim.cpp"), "-o", str(out)], check=True)
    return out


class Recorder:
    """Эмулятор, который запоминает команды (для Python-стороны)."""

    def __init__(self, emu):
        self.emu, self.sent = emu, []

    def __getattr__(self, name):
        return getattr(self.emu, name)

    def write(self, data):
        self.sent.append(data.decode().strip())
        self.emu.write(data)


def run_cpp(sim, emu, steps):
    proc = subprocess.Popen([str(sim), str(steps)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    sent = []
    while True:
        line = proc.stdout.readline().decode("utf-8").rstrip("\r\n")
        if line.startswith("CMD "):
            sent.append(line[4:])
            emu.write((line[4:] + "\r").encode())
            answer = emu.read(1 << 16)
            proc.stdin.write(answer.hex().encode() + b"\n")
            proc.stdin.flush()
        elif line.startswith("SNAP "):
            proc.wait(10)
            return sent, json.loads(line[5:])
        else:
            proc.kill()
            raise AssertionError(f"неожиданно от прошивки: {line!r}")


def run_py(emu, steps, tmp_path):
    rec = Recorder(emu)
    p = Poller(LadaProfile(ser_factory=lambda: rec, log_dir=str(tmp_path)), interval=0.2,
               record_dir=None, sleep=lambda s: None)
    p.run(max_cycles=steps)
    return rec.sent, p.snapshot(history=True)


@pytest.mark.parametrize("scenario", ["rich", "ok"])
def test_core_matches_python(sim, tmp_path, scenario):
    frozen = lambda: 5000.0  # noqa: E731 — время эмулятора стоит, значения зависят только от числа запросов
    cpp_sent, cpp = run_cpp(sim, JanuaryEmulator(scenario, clock=frozen), 7)
    py_sent, py = run_py(JanuaryEmulator(scenario, clock=frozen), 7, tmp_path)
    assert cpp_sent == py_sent
    for key in ("title", "ecu", "vin", "ident", "adapter", "voltage", "dtcs", "dtc_error", "notes", "fuel_system"):
        assert cpp["info"][key] == py["info"][key], key
    assert cpp["status"] == py["status"] == "online"
    assert cpp["samples"] == py["samples"] == 7
    assert list(cpp["values"]) == list(py["values"])
    for name, v in py["values"].items():
        c = cpp["values"][name]
        assert (c["label"], c["unit"], c["bad"]) == (v["label"], v["unit"], v["bad"]), name
        assert c["value"] == pytest.approx(v["value"]), name
    assert cpp["hints"] == py["hints"]
    assert cpp["default_tiles"] == py["default_tiles"]
    assert len(cpp["history"]) == 7


class Silent(JanuaryEmulator):
    """Блок не отвечает: инициализация K-line не проходит."""

    def _answer(self, c):
        return "BUS INIT: ...ERROR" if c in ("ATFI", "ATSI") else super()._answer(c)


def test_core_no_ecu(sim):
    sent, snap = run_cpp(sim, Silent("rich"), 1)
    assert snap["status"] == "lost" and "не отвечает на K-line" in snap["message"]
    assert "ATSH8110F1" in sent and "ATSH8111F1" in sent
    assert not any(c[:2] in ("14", "04", "2E", "31", "27") for c in sent)  # только чтение
