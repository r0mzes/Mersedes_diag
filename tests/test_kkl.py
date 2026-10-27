"""Кабель KKL напрямую и «Январь-5.1» (блок 21 01): транспорт, разбор, эмулятор, команды."""
import json

import pytest

from vito_diag import cli, january
from vito_diag.elm import Module
from vito_diag.emulator import KklJanuary51
from vito_diag.gui import build_args
from vito_diag.kkl import KklLink, frame
from vito_diag.live import LadaProfile, Poller
from vito_diag.protocol import DiagError

# Ответ настоящего блока 2026-10-27 (зажигание вкл., мотор холодный, стоит).
REAL_2101 = [0x61, 0x01] + [int(x, 16) for x in (
    "1F 82 01 04 00 00 00 00 31 4D 00 00 00 96 96 80 00 00 75 00 16 00 00 00 00 00 00 00 00 00 FF FF 40 B1").split()]


def link(emu, tmp_path):
    return KklLink("x", ser=emu, log_dir=str(tmp_path), sleep=lambda s: None)


def test_frame():
    assert frame(0x10, [0x81]).hex(" ").upper() == "81 10 F1 81 03"
    assert frame(0x10, [0x1A, 0x80]).hex(" ").upper() == "82 10 F1 1A 80 1D"
    long = frame(0x10, list(range(70)))
    assert long[:4] == bytes([0x80, 0x10, 0xF1, 70]) and len(long) == 4 + 70 + 1


def test_decode_real_block():
    v = january.decode_2101(REAL_2101)
    assert v["ECT"] == 9 and v["RPM"] == 0 and v["VOLT"] == 11.05 and v["INJ_CORR"] == 1.0
    assert v["AFR"] == 11.77 and v["IAC"] == 150 and "FUEL_100" not in v  # FF FF — нет значения
    assert january.j51_fuel_system(REAL_2101) == ""  # мотор стоит
    assert january.decode_2101([0x61, 0x01, 0x00]) == {}


def test_kkl_scan_emulator(tmp_path):
    emu = KklJanuary51("rich", clock=lambda: 5000.0)
    rep = january.scan(link(emu, tmp_path), live_count=3)
    assert rep.layout == "j51" and rep.ecu_number == "2112-1411020-41"
    assert rep.ident["sw"] == "J5V05L19" and rep.ident["date"] == "01-01-2003"
    assert [d.code for d in rep.dtcs] == ["P0172"]
    assert emu.sent[:2] == ["81", "1A 80"]
    assert all(s.split()[0] in ("81", "82", "1A", "18", "01", "21") for s in emu.sent)  # только чтение
    s = rep.samples[-1]
    assert s["ECT"] == 62 and s["AFR"] == 12.4 and 14 < s["MAF"] < 17


def test_kkl_no_init_no_answer(tmp_path):
    class Deaf(KklJanuary51):
        def _answer(self, req):
            return None

    lk = link(Deaf(), tmp_path)
    assert not lk.kline_connect(0x10)
    with pytest.raises(DiagError):
        lk.request(Module("kline", 0x10), bytes([0x21, 0x01]))
    with pytest.raises(DiagError):
        lk.request(Module("kline", 0x10), bytes([0x14, 0x00, 0x00]))  # стирание ошибок — блокируется


def test_kkl_profile_hints(tmp_path):
    t = {"now": 0.0}
    profile = LadaProfile(ser_factory=lambda: KklJanuary51("rich", clock=lambda: t["now"]),
                          kkl=True, log_dir=str(tmp_path))
    info = profile.open()
    assert info["ecu"] == "2112-1411020-41" and "J5V05L19" in info["ident"]
    t["now"] = 200  # прогрелся
    samples = [profile.sample() for _ in range(6)]
    assert profile.fuel_system == "работа по лямбда-зонду"
    hints = " ".join(profile.hints(samples))
    assert "задаёт богатую смесь" in hints and "урезает впрыск на 14 %" in hints
    profile.close()

    t["now"] = 0.0
    ok = LadaProfile(ser_factory=lambda: KklJanuary51("ok", clock=lambda: t["now"]), kkl=True, log_dir=str(tmp_path))
    ok.open()
    t["now"] = 200
    assert "явных причин" in " ".join(ok.hints([ok.sample() for _ in range(6)]))


def test_kkl_poller_and_cli(tmp_path, monkeypatch, capsys):
    p = Poller(LadaProfile(ser_factory=lambda: KklJanuary51("rich"), kkl=True, log_dir=str(tmp_path)),
               interval=0, record_dir=None, sleep=lambda s: None)
    p.run(max_cycles=3)
    snap = p.snapshot()
    assert snap["status"] == "online" and "AFR" in snap["values"] and snap["values"]["AFR"]["unit"] == ""

    import vito_diag.kkl as kkl

    monkeypatch.setattr(kkl.KklLink, "__init__", lambda self, port, **kw: KklLink.__wrapped_init__(
        self, port, ser=KklJanuary51("rich"), log_dir=str(tmp_path), sleep=lambda s: None))
    assert cli.main(["lada", "--kkl", "--port", "COMX", "--count", "2", "--reports-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "прошивка: J5V05L19" in out and "Заданный состав смеси" in out and "P0172" in out
    report = json.loads(next(tmp_path.glob("lada_*.json")).read_text(encoding="utf-8"))
    assert report["layout"] == "j51" and len(report["samples"]) == 2


KklLink.__wrapped_init__ = KklLink.__init__


def test_web_kkl_demo_and_gui_args():
    args = cli.build_parser().parse_args(["web", "--kkl", "--demo"])
    profile = cli.make_profile(args)
    assert profile.kkl and isinstance(profile, LadaProfile)
    assert build_args("lada", "COM9", "38400", hub="COM4", kkl=True) == ["lada", "--port", "COM9", "--kkl",
                                                                        "--count", "20"]
    assert build_args("web-lada", "COM9", "38400", hub="COM4", kkl=True) == ["web", "--profile", "lada", "--kkl",
                                                                            "--port", "COM9"]
    assert build_args("web-lada", demo=True, kkl=True) == ["web", "--profile", "lada", "--kkl", "--demo"]
