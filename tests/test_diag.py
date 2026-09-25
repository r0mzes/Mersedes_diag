import json

import pytest

from vito_diag.analyzer import analyze, check_live
from vito_diag.cli import main
from vito_diag.dtc import bytes_to_dtc, is_manufacturer_code, load_database, lookup


def test_bytes_to_dtc():
    assert bytes_to_dtc(0x01, 0x23) == "P0123"
    assert bytes_to_dtc(0x42, 0x99) == "C0299"
    assert bytes_to_dtc(0x81, 0x00) == "B0100"
    assert bytes_to_dtc(0xC1, 0x00) == "U0100"
    assert bytes_to_dtc(0x24, 0x63) == "P2463"


def test_database_entries_valid():
    db = load_database()
    assert len(db) > 50
    for code, entry in db.items():
        assert len(code) == 5 and code[0] in "PCBU"
        assert entry["severity"] in ("critical", "high", "medium", "low")
        assert entry["description"] and entry["advice"]


def test_lookup_known_and_unknown():
    assert lookup("p0299").severity == "high"
    unknown = lookup("P1F00")
    assert unknown.severity == "unknown"
    assert "Mercedes" in unknown.description
    assert is_manufacturer_code("P1F00") and not is_manufacturer_code("P2463")
    assert is_manufacturer_code("C1500") and not is_manufacturer_code("U0100")


def test_analyze_patterns():
    a = analyze([("P0299", ""), ("P0101", ""), ("P2453", "")], [("P0671", "")])
    titles = [f.title for f in a.findings]
    assert any("наддув" in t for t in titles)
    assert any("DPF" in t for t in titles)
    assert any("накала" in t for t in titles)
    assert a.worst_severity == "high"
    assert a.dtcs[0].severity == "high"


def test_analyze_network_and_empty():
    a = analyze([("U0100", ""), ("U0121", ""), ("P0562", "")])
    assert any("питание" in f.title for f in a.findings)
    assert analyze([]).worst_severity == "none"


def test_check_live_voltage():
    live = {"RPM": ("", 800.0, "rpm"), "CONTROL_MODULE_VOLTAGE": ("", 12.5, "V")}
    assert check_live(live)
    live["CONTROL_MODULE_VOLTAGE"] = ("", 14.1, "V")
    assert not check_live(live)


def test_cli_demo_scan(tmp_path, capsys):
    assert main(["scan", "--demo", "--reports-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "P0299" in out
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert {d["code"] for d in report["dtcs"]} >= {"P0299", "P2463"}
    assert next(tmp_path.glob("*.html")).stat().st_size > 500


def test_cli_demo_clear(capsys):
    assert main(["clear", "--demo", "--yes"]) == 0
