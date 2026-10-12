import pytest

from tests.fake_elm import FakeElm
from tests.fake_switch import FakeSwitch
from vito_diag import cli
from vito_diag.elm import ElmLink
from vito_diag.switch import LineSwitch, SwitchError, parse_meas


def test_switch_commands():
    fake = FakeSwitch()
    sw = LineSwitch("fake", ser=fake)
    sw.select(9)
    assert fake.line == "9"
    with pytest.raises(SwitchError):
        sw.select("3")
    assert "SEL 3" not in fake.sent  # неверная линия не уходит в Arduino
    sw.close()
    assert fake.sent[-1] == "RESET"


def test_measure_and_verdict():
    r = parse_meas("MEAS 16:12.45:12.40:12.50 7:12.10:1.20:12.30 8:0.01:0.00:0.02 6:2.51:2.30:3.40")
    assert r[16].verdict().startswith("~12")
    assert "обмен" in r[7].verdict()
    assert r[8].verdict().startswith("0 В")
    assert "CAN" in r[6].verdict()
    with pytest.raises(SwitchError):
        parse_meas("OK")


def test_ecu_scan_kline_over_all_lines(tmp_path, monkeypatch):
    fake_sw = FakeSwitch()
    fake_elm = FakeElm()
    monkeypatch.setattr("vito_diag.switch.open_switch",
                        lambda port: LineSwitch(port, ser=fake_sw))
    monkeypatch.setattr("vito_diag.elm.ElmLink",
                        lambda port, **kw: ElmLink(port, ser=fake_elm, log_dir=str(tmp_path), timeout=0.2))
    rc = cli.main(["ecu", "scan-kline", "--port", "fake", "--switch", "fake-sw",
                   "--line", "7,9", "--addrs", "10", "--reports-dir", str(tmp_path)])
    assert rc == 0
    sels = [c for c in fake_sw.sent if c.startswith("SEL")]
    assert sels == ["SEL 7", "SEL 9"]
    assert fake_sw.sent[-1] == "RESET"  # после работы — обратно на стандартную линию
    report = next(tmp_path.glob("modules_*.json")).read_text(encoding="utf-8")
    assert '"line": "9"' in report


def test_multiple_lines_need_switch(capsys):
    assert cli.main(["ecu", "scan-kline", "--port", "x", "--line", "7,8"]) == 1


def _bridge_env(monkeypatch, tmp_path):
    from tests.fake_bridge import FakeBridge

    bridge = FakeBridge()
    monkeypatch.setattr("vito_diag.switch.time.sleep", lambda s: None)
    monkeypatch.setattr("vito_diag.elm.open_serial", lambda port, **kw: bridge)
    monkeypatch.setattr("vito_diag.elm.ElmLink",
                        lambda port, **kw: ElmLink(port, ser=kw["ser"], log_dir=str(tmp_path), timeout=0.2))
    return bridge


def test_ecu_scan_kline_through_bridge(tmp_path, monkeypatch):
    from tests.fake_bridge import ELM, HC06

    bridge = _bridge_env(monkeypatch, tmp_path)
    rc = cli.main(["ecu", "scan-kline", "--port", "COM7", "--switch", "bt",
                   "--line", "7,9", "--addrs", "10", "--reports-dir", str(tmp_path)])
    assert rc == 0
    sels = [c for c in bridge.sw.sent if c.startswith("SEL")]
    assert sels == ["SEL 9"]  # линия 7 уже стоит — лишний раз к HC-06 не ходим
    assert bridge.sw.sent[-1] == "RESET"
    assert bridge.addr == ELM and bridge.connected  # мост вернулся к адаптеру
    assert HC06 in bridge.uses
    # перед уходом к переключателю сессия K-line закрыта
    assert "ATPC" in bridge.elm.sent
    report = next(tmp_path.glob("modules_*.json")).read_text(encoding="utf-8")
    assert '"line": "9"' in report


def test_switch_sel_through_bridge(tmp_path, monkeypatch, capsys):
    from tests.fake_bridge import ELM

    bridge = _bridge_env(monkeypatch, tmp_path)
    assert cli.main(["switch", "sel", "11", "--switch", "bt", "--port", "COM7"]) == 0
    assert bridge.sw.line == "11"
    assert "RESET" not in bridge.sw.sent  # выбранная линия остаётся
    assert bridge.addr == ELM
    assert "STATE SEL 11" in capsys.readouterr().out


def test_bridge_switch_without_port(capsys):
    assert cli.main(["switch", "state", "--switch", "bt"]) == 1
