import pytest

from tests.fake_elm import FakeElm
from vito_diag.elm import ElmLink, Module
from vito_diag.protocol import (
    DiagError, extract_part_number, is_read_only, parse_can_response, parse_hex_request,
    parse_kline_response, parse_kwp_dtc_report, parse_uds_dtc_report,
)


def make_link(tmp_path, unsafe=False):
    fake = FakeElm()
    return ElmLink("fake", ser=fake, log_dir=str(tmp_path), unsafe=unsafe, timeout=0.2), fake


def test_parse_can_single_and_multi():
    assert parse_can_response("59 02 FF 01 23 45 08\r\r>") == [0x59, 0x02, 0xFF, 0x01, 0x23, 0x45, 0x08]
    data = parse_can_response("00B\r0: 59 02 FF 01 23 45\r1: 08 C1 00 00 09 00 00\r\r>")
    assert len(data) == 11
    assert [r[0] for r in parse_uds_dtc_report(data)] == ["P0123", "U0100"]
    with pytest.raises(DiagError):
        parse_can_response("NO DATA\r>")


def test_parse_kline_and_kwp_dtcs():
    msgs = parse_kline_response("83 F1 10 7F 18 78 00\r85 F1 10 58 01 07 15 E0 00\r")
    assert msgs == [[0x7F, 0x18, 0x78], [0x58, 0x01, 0x07, 0x15, 0xE0]]
    assert parse_kwp_dtc_report(msgs[1]) == [("P0715", "0715", 0xE0)]
    with pytest.raises(DiagError) as e:
        parse_kwp_dtc_report([0x7F, 0x18, 0x11])
    assert e.value.nrc == 0x11


def test_read_only_guard():
    assert is_read_only(parse_hex_request("18 02 FF 00"))
    assert is_read_only(parse_hex_request("1A86"))
    assert not is_read_only(parse_hex_request("14 FF 00"))   # стирание ошибок
    assert not is_read_only(parse_hex_request("31 01"))      # запуск процедур
    assert not is_read_only(parse_hex_request("10 85"))      # сессия программирования
    assert not is_read_only(parse_hex_request("04"))         # OBD стирание


def test_part_number():
    assert extract_part_number(list(b"ZZA6461234567")) == "A 646 123 45 67"
    assert extract_part_number([0x5A, 0x86, 0x64, 0x64, 0x46, 0x12, 0x34]).endswith("(?)")


def test_scan_and_read_can(tmp_path):
    link, _ = make_link(tmp_path)
    found = link.scan_can(0x7DE, 0x7E2)
    assert [(m.address, m.reply) for m in found] == [(0x7E0, 0x7E8)]
    m = found[0]
    link.identify(m)
    assert m.part_number == "A 646 123 45 67"
    assert m.protocol == "kwp"
    link.read_dtcs(m)
    assert [d.code for d in m.dtcs] == ["P0401", "P2034"]
    link.close()
    assert "3E00" in link.log_path.read_text(encoding="utf-8")


def test_scan_and_read_kline(tmp_path):
    link, _ = make_link(tmp_path)
    found = link.scan_kline(range(0x0E, 0x12), line="9")
    assert [(m.address, m.line) for m in found] == [(0x10, "9")]
    link.read_dtcs(found[0])
    assert [d.code for d in found[0].dtcs] == ["P0715"]
    link.close()


def test_unsafe_request_blocked(tmp_path):
    link, fake = make_link(tmp_path)
    with pytest.raises(DiagError):
        link.request(Module("can", 0x7E0, 0x7E8), bytes([0x14, 0xFF, 0x00]))
    assert "14FF00" not in fake.sent
    link.close()
