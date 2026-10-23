"""Ядро живых данных, эмулятор «Января» и веб-панель."""
import json
import threading
import urllib.request

import pytest

from vito_diag import cli
from vito_diag.emulator import JanuaryEmulator
from vito_diag.gui import build_args
from vito_diag.live import LadaProfile, ObdProfile, Poller


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_emulator_full_protocol(tmp_path):
    clock = Clock()
    profile = LadaProfile(ser_factory=lambda: JanuaryEmulator("rich", clock=clock), log_dir=str(tmp_path))
    info = profile.open()
    assert info["ecu"] == "2112-1411020-75" and info["fuel_system"] == "работа по лямбда-зонду"
    assert [d["code"] for d in info["dtcs"]] == ["P0172"]
    assert "богатая" in info["dtcs"][0]["description"]
    clock.t += 120  # мотор прогрелся
    s = profile.sample()
    assert s["ECT"] == 90 and round(s["LTFT"]) == -18 and 14 < s["MAF"] < 17 and s["VOLT"] > 13
    hints = " ".join(profile.hints([profile.sample() for _ in range(6)]))
    assert "убирает топливо" in hints and "постоянно богатая" in hints
    profile.close()

    ok = LadaProfile(ser_factory=lambda: JanuaryEmulator("ok", clock=clock), log_dir=str(tmp_path))
    assert ok.open()["dtcs"] == []
    clock.t += 200
    assert "явных причин" in " ".join(ok.hints([ok.sample() for _ in range(8)]))
    with pytest.raises(ValueError):
        JanuaryEmulator("нет такого")


class Flaky:
    """Профиль: первое подключение не удаётся, потом два замера и обрыв, потом снова на связи."""
    kind = "lada"
    meta = {"RPM": ("Обороты", "об/мин"), "ECT": ("ОЖ", "°C")}

    def __init__(self):
        self.opens = self.n = 0
        self.closed = 0

    def open(self):
        self.opens += 1
        if self.opens == 1:
            raise ConnectionError("BUS INIT: UNABLE TO CONNECT")
        return {"title": "тест", "dtcs": []}

    def sample(self):
        self.n += 1
        if self.n == 3:
            raise ConnectionError("NO DATA")
        return {"RPM": 800.0, "ECT": 60.0}

    def hints(self, samples):
        return [f"замеров {len(samples)}"]

    def close(self):
        self.closed += 1


def test_poller_reconnects_and_marks_stale(tmp_path):
    clock, sleeps = Clock(), []
    profile = Flaky()
    p = Poller(profile, interval=0, stale_after=5, record_dir=str(tmp_path), clock=clock,
               sleep=sleeps.append)
    p.run(max_cycles=6)
    assert profile.opens == 3 and p.reconnects == 1 and sleeps == [1.0]
    assert p.status == "online" and p.samples == 4
    snap = p.snapshot(history=True)
    assert snap["values"]["ECT"]["bad"] and not snap["values"]["ECT"]["stale"]  # 60 °C на холостом
    assert snap["hints"] == ["замеров 4"] and len(snap["history"]) == 4
    clock.t += 10
    assert p.snapshot()["values"]["RPM"]["stale"]
    rows = open(p.csv_path, encoding="utf-8").read().splitlines()
    assert rows[0] == "time;RPM;ECT" and len(rows) == 5


def test_obd_demo_profile(tmp_path):
    p = Poller(ObdProfile(demo=True), interval=0, record_dir=None, sleep=lambda s: None)
    p.run(max_cycles=2)
    snap = p.snapshot()
    assert snap["status"] == "online" and "P0299" in [d["code"] for d in snap["info"]["dtcs"]]
    assert snap["values"]["RPM"]["unit"] == "rpm"


def test_web_server(tmp_path):
    from vito_diag.web import serve

    profile = LadaProfile(ser_factory=lambda: JanuaryEmulator("rich"), log_dir=str(tmp_path))
    poller = Poller(profile, interval=0.05, record_dir=None)
    poller.start()
    started = threading.Event()
    box = {}

    def ready(server):
        box["server"] = server
        started.set()

    threading.Thread(target=serve, args=(poller, "127.0.0.1", 0, ready), daemon=True).start()
    assert started.wait(5)
    port = box["server"].server_address[1]
    try:
        for _ in range(50):
            state = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5).read())
            if state["samples"] >= 2:
                break
            poller.wait(state["version"], 0.2)
        assert state["status"] == "online" and state["values"]["LTFT"]["bad"]
        assert state["default_tiles"][0] == "ECT" and state["history"]
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "EventSource" in page and "<title>" in page
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stream", timeout=5) as r:
            line = r.readline().decode()
        assert line.startswith("data: ") and json.loads(line[6:])["profile"] == "lada"
    finally:
        box["server"].shutdown()
        poller.stop()


def test_cli_and_gui_args():
    args = cli.build_parser().parse_args(["web", "--demo"])
    assert args.demo == "rich" and isinstance(cli.make_profile(args), LadaProfile)
    args = cli.build_parser().parse_args(["web"])
    with pytest.raises(ValueError):
        cli.make_profile(args)
    assert isinstance(cli.make_profile(cli.build_parser().parse_args(["web", "--profile", "obd", "--demo"])),
                      ObdProfile)
    assert build_args("web-lada", "COM7", hub="COM4") == ["web", "--profile", "lada", "--port", "COM7",
                                                         "--hub", "COM4"]
    assert build_args("web-obd", "COM7", hub="COM4") == ["web", "--profile", "obd", "--port", "COM7"]
    assert build_args("web-lada", demo=True) == ["web", "--profile", "lada", "--demo"]
    with pytest.raises(ValueError):
        build_args("web-obd")
