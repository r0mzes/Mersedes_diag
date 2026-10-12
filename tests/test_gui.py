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
