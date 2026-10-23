import pytest

from vito_diag.gui import build_args


def test_build_args():
    assert build_args("scan", "COM7", "38400") == ["scan", "--port", "COM7", "--baudrate", "38400"]
    assert build_args("scan-all", "COM7", "", line="9") == ["ecu", "scan-all", "--port", "COM7", "--line", "9"]
    assert build_args("read", "COM7", "", block="12")[-4:] == ["--kline", "12", "--line", "7"]
    assert "--can" in build_args("read", "COM7", "", block="7E1:7E9")
    assert build_args("scan", "COM7", "38400", demo=True) == ["scan", "--demo", "--no-save"]
    with pytest.raises(ValueError):
        build_args("read", "COM7", "", block="")


def test_saved_results(tmp_path):
    import json

    from vito_diag.gui import format_saved, saved_results

    (tmp_path / "vito_2026-09-30_21-26-56.json").write_text(json.dumps({
        "created": "2026-09-30T21:26:56", "vehicle": {"Порт": "COM7"}, "verdict": "есть ошибки",
        "dtcs": [{"code": "P0730", "description": "Неверное передаточное отношение"}], "findings": []}),
        encoding="utf-8")
    (tmp_path / "vito_2026-09-30_22-31-15.json").write_text(json.dumps({
        "created": "2026-09-30T22:31:15", "vehicle": {"Порт": "симулятор"}, "dtcs": []}), encoding="utf-8")
    (tmp_path / "modules_2026-09-30_22-22-37.json").write_text(json.dumps({
        "created": "2026-09-30T22:22:37", "modules": [{
            "bus": "kline", "address": "0x12", "reply": "", "line": "7", "protocol": "kwp",
            "notes": [], "error": "",
            "dtcs": [{"code": "P2232", "description": "?",
                      "extra": {"status_byte": "0x60", "status": ["сохранена"]}}]}]}), encoding="utf-8")
    items = {p.name: (title, demo) for p, title, demo in saved_results(tmp_path)}
    assert items["vito_2026-09-30_21-26-56.json"] == (
        "30.09 21:26  Скан двигателя (OBD-II) — ошибок: 1 [P0730]", False)
    assert items["vito_2026-09-30_22-31-15.json"][1] is True
    assert "блоков: 1, ошибок: 1 [P2232]" in items["modules_2026-09-30_22-22-37.json"][0]
    text = format_saved(tmp_path / "modules_2026-09-30_22-22-37.json")
    assert "K-line (конт. 7) 0x12" in text and "P2232 (статус 0x60" in text


def test_build_args_with_bridge_switch():
    from vito_diag.gui import ALL_LINES, build_args

    assert build_args("scan-all", "COM7", line=ALL_LINES, switch=True) == \
        ["ecu", "scan-all", "--port", "COM7", "--switch", "bt", "--line", ALL_LINES]
    assert build_args("switch-meas", "COM7") == ["switch", "meas", "--switch", "bt", "--port", "COM7"]
    with pytest.raises(ValueError):
        build_args("scan-all", "COM7", line=ALL_LINES)  # все линии — только с переключателем


def test_bridge_exchange_cancel():
    import threading
    import time

    from vito_diag.gui import bridge_exchange

    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.time()
    out = bridge_exchange("loop://", [("~STATE", 2), ("ATI", 2), ("AT RV", 2)], cancel=cancel)
    assert time.time() - start < 1.5
    assert out.startswith(">> ~STATE") and "ATI" not in out


def test_build_args_modules():
    assert build_args("module", hub="COM4", block="7E1:7E9") == [
        "ecu", "read", "--hub", "COM4", "--can", "7E1:7E9", "--details"]
    assert build_args("all-modules", hub="COM4", details=False) == ["ecu", "scan-can", "--hub", "COM4"]
    with pytest.raises(ValueError):
        build_args("module", block="7E1:7E9")


def test_lada_args_and_saved(tmp_path):
    import json

    from vito_diag.gui import format_saved, saved_results

    assert build_args("lada", "COM7", "", hub="COM4", count="5") == \
        ["lada", "--port", "COM7", "--hub", "COM4", "--count", "5"]
    assert build_args("lada", "COM7", "38400") == ["lada", "--port", "COM7", "--baudrate", "38400",
                                                    "--count", "20"]
    with pytest.raises(ValueError):
        build_args("lada", "COM7", demo=True)
    (tmp_path / "lada_2026-10-24_10-00-00.json").write_text(json.dumps({
        "created": "2026-10-24T10:00:00", "address": "0x10", "ecu_number": "2112-1411020-82",
        "dtcs": [{"code": "P0300", "description": "Пропуски", "status_byte": "0xE0", "status": ["есть"]}],
        "dtc_error": "", "fuel_system": "работа по лямбда-зонду",
        "samples": [{"RPM": 800, "ECT": 90, "LTFT": -20, "STFT": -5}]}), encoding="utf-8")
    (path, title, demo), = saved_results(tmp_path)
    assert title == "24.10 10:00  ВАЗ «Январь» 2112-1411020-82 — ошибок: 1 [P0300]" and not demo
    text = format_saved(path)
    assert "P0300 (статус 0xE0" in text and "убирает топливо" in text and "Температура ОЖ" in text


def test_lada_watch_args_and_dashboard():
    import tkinter as tk

    from vito_diag.gui import LadaDashboard

    assert build_args("lada-watch", "COM7", hub="COM4") == ["lada", "--port", "COM7", "--hub", "COM4", "--watch"]
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("нет дисплея")
    try:
        dash = LadaDashboard(root)
        for v in (0.1, 0.8, 0.2):
            dash.add({"RPM": 800, "ECT": 20, "LTFT": -20, "STFT": 0, "O2S1": v,
                      "fuel_system": "работа по лямбда-зонду"})
        value, graph = dash.tiles["ECT"]
        assert value["text"] == "20" and value["fg"] == "#c62828"
        assert dash.tiles["LTFT"][0]["text"] == "-20.0" and graph.find_all()
        assert "считает мотор холодным" in dash.hints["text"]
        assert dash.tiles["MAF"][0]["text"] == "—"
        dash.reset()
        assert value["text"] == "—" and not graph.find_all()
    finally:
        root.destroy()
