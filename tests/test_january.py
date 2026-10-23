"""ВАЗ «Январь» на эмуляторе: опознание, ошибки, живые данные и подсказки по смеси."""
import json

from tests.fake_elm import FakeElm
from vito_diag import cli, january
from vito_diag.elm import ElmLink

VALUES = {0x03: [0x02, 0x00], 0x04: [0x40], 0x05: [0x82], 0x06: [0x78], 0x07: [0x60],
          0x0C: [0x0C, 0x80], 0x0D: [0x00], 0x0E: [0x94], 0x0F: [0x3C], 0x10: [0x01, 0xF4],
          0x11: [0x00], 0x14: [0xB4, 0xFF]}


def _frame(data):
    """Сообщение KWP2000 от блока 0x10 к тестеру: 80 F1 10 <длина> <данные> <КС>."""
    body = [0x80, 0xF1, 0x10, len(data)] + list(data)
    return " ".join(f"{b:02X}" for b in body + [sum(body) & 0xFF])


class JanuaryElm(FakeElm):
    """Январь 7.2: отвечает только на 0x10, быстрая инициализация."""

    def _kline(self, c):
        if not self.kline_ok:
            return "NO DATA"
        if c == "1A80":
            text = b"XTA21124070123456  2112-1411020-82  J7ES"
            return _frame([0x5A, 0x80] + list(text))
        if c == "1800FF00":
            return _frame([0x58, 0x01, 0x03, 0x00, 0xE0])
        if c == "0100":
            mask = 0
            for pid in VALUES:
                mask |= 1 << (32 - pid)
            return _frame([0x41, 0x00] + list(mask.to_bytes(4, "big")))
        if c.startswith("01") and int(c[2:], 16) in VALUES:
            pid = int(c[2:], 16)
            return _frame([0x41, pid] + VALUES[pid])
        if c == "2101":
            return _frame([0x61, 0x01, 0x55, 0x00])
        return _frame([0x7F, int(c[:2], 16), 0x11])


def test_january_scan(tmp_path):
    link = ElmLink("x", ser=JanuaryElm(), log_dir=str(tmp_path))
    rep = january.scan(link, live_count=3)
    assert rep.address == 0x10
    assert rep.vin == "XTA21124070123456"
    assert rep.ecu_number == "2112-1411020-82"
    assert [d.code for d in rep.dtcs] == ["P0300"]
    assert rep.fuel_system == "работа по лямбда-зонду"
    s = rep.samples[0]
    assert s["ECT"] == 90 and s["RPM"] == 800 and s["LTFT"] == -25 and s["MAF"] == 18
    assert s["O2S1"] == 0.9
    assert rep.raw_2101 == ["61 01 55 00"] * 3
    hints = " ".join(january.check_mixture(rep.samples, rep.fuel_system))
    assert "убирает топливо" in hints and "постоянно богатая" not in hints  # мало замеров
    assert "ДМРВ показывает 18.0" in hints


def test_mixture_hints():
    cold = [{"RPM": 800, "ECT": 20, "LTFT": 0, "STFT": 0, "O2S1": 0.85, "TPS": 0}] * 6
    hints = " ".join(january.check_mixture(cold))
    assert "считает мотор холодным" in hints and "постоянно богатая" in hints
    assert "не работал" in january.check_mixture([{"RPM": 0, "ECT": 20}])[0]
    ok = [{"RPM": 800, "ECT": 90, "LTFT": 2, "STFT": 1, "O2S1": v, "TPS": 0, "MAF": 9}
          for v in (0.1, 0.8, 0.2, 0.7, 0.1)]
    assert "явных причин перелива не видно" in january.check_mixture(ok)[0]


def test_january_no_answer(tmp_path):
    link = ElmLink("x", ser=FakeElm(), log_dir=str(tmp_path))
    try:
        january.scan(link, addresses=[0x11])
    except january.DiagError as e:
        assert "0x11" in str(e)
    else:
        raise AssertionError("ожидалась ошибка")


def test_cli_lada(tmp_path, monkeypatch, capsys):
    import vito_diag.elm as elm

    monkeypatch.setattr(elm, "open_serial", lambda *a, **k: JanuaryElm())
    monkeypatch.chdir(tmp_path)
    assert cli.main(["lada", "--port", "COMX", "--count", "2", "--reports-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2112-1411020-82" in out and "P0300" in out and "Смесь" in out
    report = json.loads(next(tmp_path.glob("lada_*.json")).read_text(encoding="utf-8"))
    assert report["ecu_number"] == "2112-1411020-82" and len(report["samples"]) == 2


def test_cli_lada_watch(tmp_path, monkeypatch, capsys):
    import vito_diag.elm as elm

    monkeypatch.setattr(elm, "open_serial", lambda *a, **k: JanuaryElm())
    calls = []
    real = january.read_2101

    def stop_after_three(link, module):
        calls.append(1)
        if len(calls) > 3:
            raise KeyboardInterrupt
        return real(link, module)

    monkeypatch.setattr(january, "read_2101", stop_after_three)
    assert cli.main(["lada", "--port", "COMX", "--watch", "--reports-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    live = [json.loads(l[5:]) for l in out.splitlines() if l.startswith("LIVE ")]
    assert len(live) == 3 and live[0]["ECT"] == 90 and live[0]["fuel_system"] == "работа по лямбда-зонду"
    assert out.index("2112-1411020-82") < out.index("LIVE ")  # опознание и ошибки — до живых данных
    rows = next(tmp_path.glob("lada_live_*.csv")).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 4 and rows[0].startswith("t;LOAD;ECT")
