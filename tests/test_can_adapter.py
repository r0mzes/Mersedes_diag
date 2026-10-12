"""vito_diag + ядро самодельного CAN-адаптера (firmware/esp32_can_adapter) + имитация машины.

Прошивку собираем для компьютера вместе с tests/can_sim.cpp и гоняем через неё настоящий ElmLink:
так проверяется, что vito_diag и адаптер понимают друг друга, без ESP32 и без машины.
"""

import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from vito_diag.elm import ElmLink, Module

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="нужен g++")


@pytest.fixture(scope="module")
def sim_binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("can_sim") / "can_sim"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Werror", "-O1",
                    "-I", str(ROOT / "firmware" / "esp32_can_adapter"),
                    str(ROOT / "tests" / "can_sim.cpp"), "-o", str(out)], check=True)
    return out


class ProcessSerial:
    """Процесс-имитатор с интерфейсом pyserial, которым пользуется ElmLink."""

    def __init__(self, binary):
        self.proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.buf = b""
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            b = self.proc.stdout.read1(4096)
            if not b:
                return
            with self.lock:
                self.buf += b

    @property
    def in_waiting(self):
        with self.lock:
            return len(self.buf)

    def read(self, n=1):
        for _ in range(100):
            with self.lock:
                if self.buf:
                    chunk, self.buf = self.buf[:n], self.buf[n:]
                    return chunk
            threading.Event().wait(0.001)
        return b""

    def write(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def reset_input_buffer(self):
        with self.lock:
            self.buf = b""

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


@pytest.fixture
def link(sim_binary, tmp_path):
    ser = ProcessSerial(sim_binary)
    lk = ElmLink("sim", ser=ser, log_dir=str(tmp_path), timeout=1.0)
    yield lk
    lk.close()
    # Код выхода 1 — ядро пыталось передать кадр в режиме «только слушать».
    assert ser.proc.returncode == 0


def test_version_and_voltage(link):
    assert "vito-can" in link.version
    assert link.voltage() == "12.6V"


def test_long_reply_is_complete(link):
    payload = link.can_request(0x7E1, 0x7E9, bytes.fromhex("1A86"))
    assert not link.truncated
    assert len(payload) == 18
    assert payload[:6] == [0x5A, 0x86, 0x00, 0x34, 0x46, 0x41]


def test_response_pending_then_long_reply(link):
    payload = link.can_request(0x7E1, 0x7E9, bytes.fromhex("1A87"))
    assert not link.truncated
    assert bytes(payload[-9:]) == b"A00344641"


def test_dtcs_read_in_one_request(link):
    module = Module("can", 0x7E1, 0x7E9)
    link.read_dtcs(module)
    assert [d.extra["raw"] for d in module.dtcs] == ["1731", "D40B"]
    assert not module.notes


def test_custom_request_id(link):
    """Блок 0x740 отвечает только на свой ID — у клона ELM327 до него не достучаться."""
    payload = link.can_request(0x740, 0x748, bytes.fromhex("1A86"))
    assert len(payload) == 62 and payload[2:] == list(range(60))


def test_block_size_flow_control(link):
    """Блок шлёт по 8 кадров и ждёт следующего Flow Control."""
    link.can_request(0x740, 0x748, bytes.fromhex("3E01"))
    link.cmd("ATFCSD300800")
    text = link.cmd("1A86")
    assert text.splitlines()[0].strip() == "03E"
    assert text.splitlines()[-1].split(":")[0].strip() == "8"


def test_scan_finds_hidden_block(link):
    found = link.scan_can(0x73E, 0x742)
    assert link.can_header_ignored is False
    assert [(m.address, m.reply) for m in found] == [(0x740, 0x748)]


def test_monitor_hears_bus(link):
    ids = link.monitor_can(0.5)
    assert ids.get(0x240, 0) >= 3
    # после мониторинга адаптер снова отвечает на команды
    assert link.cmd("ATI").endswith("vito-can 0.1)")


def test_no_data_for_silent_id(link):
    assert link.cmd("ATSH7A0") == "OK"
    link.cmd("ATCRA7A8")
    assert link.cmd("1A86") == "NO DATA"


def test_identify_not_truncated(link):
    module = Module("can", 0x7E1, 0x7E9)
    link.identify(module)
    assert not module.notes
    assert "…" not in module.ident_raw["1A86"]
    assert module.ident_raw["1A87"].endswith("41 30 30 33 34 34 36 34 31")


def test_kline_not_supported(link):
    assert link.has_kline is False
