import pytest

from tests.fake_elm import CloneElm, FakeElm
from vito_diag.elm import ElmLink, Module
from vito_diag.protocol import (
    DiagError, extract_part_number, is_read_only, parse_can_response, parse_can_response_ex,
    final_payload, parse_hex_request, parse_kline_response, parse_kwp_dtc_report,
    parse_uds_dtc_report,
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


def test_clone_can_header_ignored(tmp_path):
    link = ElmLink("fake", ser=CloneElm(), log_dir=str(tmp_path), timeout=0.2)
    found = link.scan_can()  # без перебора 0x400..0x7FF
    assert link.can_header_ignored
    assert [(m.address, m.reply) for m in found] == [(0x7E1, 0x7E9)]
    link.close()


def test_clone_truncated_replies(tmp_path):
    link = ElmLink("fake", ser=CloneElm(), log_dir=str(tmp_path), timeout=0.2)
    m = Module("can", 0x7E1, 0x7E9)
    link.identify(m)
    assert m.ident_raw["1A86"].endswith("(обрезано)")
    assert m.part_number == ""
    link.read_dtcs(m)
    assert sorted(d.code for d in m.dtcs) == ["P1731", "U140B"]
    assert not any("прочитано: " in n for n in m.notes)  # все 2 ошибки прочитаны по группам
    link.close()


def test_parse_can_truncated():
    data, total = parse_can_response_ex("008\r0: 58 02 D4 0B 60 17\r\r>")
    assert (len(data), total) == (6, 8)
    assert parse_kwp_dtc_report(data) == [("U140B", "D40B", 0x60)]


def test_clone_kline_without_atfi(tmp_path):
    link = ElmLink("fake", ser=CloneElm(), log_dir=str(tmp_path), timeout=0.2)
    found = link.scan_kline(range(0x0E, 0x12))
    assert link.no_fast_init and link.kline_header_ignored
    assert [m.address for m in found] == [0x10]
    link.read_dtcs(found[0])
    assert [d.code for d in found[0].dtcs] == ["P0715"]
    link.close()


def test_kline_segmented_reply_merged():
    text = ("83 F1 12 7F 1A 78 97\r87 F1 12 5A 90 57 44 46 36 33 BE\r83 F1 12 7F 1A 78 97\r"
            "87 F1 12 5A 90 30 30 30 30 30 77\r84 F1 12 5A 90 31 32 DB\r")
    payload = final_payload(parse_kline_response(text), 0x1A)
    assert bytes(payload[2:]).decode() == "WDF630000012"


def test_clone_slow_init_without_atsi(tmp_path):
    """Блок просыпается только от 5-бод инициализации, а клон не знает ATSI."""
    from tests.fake_elm import CloneElm

    class SlowOnly(CloneElm):
        def _answer(self, c):
            if not c.startswith("AT") and self.protocol == "5" and not self.kline_ok:
                return "BUS INIT: ...ERROR"
            return super()._answer(c)

    fake = SlowOnly()
    link = ElmLink("fake", ser=fake, log_dir=str(tmp_path), timeout=0.2)
    found = link.scan_kline([0x10], slow=True)
    assert [m.address for m in found] == [0x10]
    assert link.no_slow_init
    assert "ATIIA10" in fake.sent


def test_ecu_catalog_and_1a86():
    from vito_diag.ecus import decode_1a86, find_ecu

    egs = bytes.fromhex("5A86003446411048060708080353000807 01".replace(" ", ""))
    info = decode_1a86(list(egs))
    assert info["part"] == "A 003 446 41 10"
    assert info["hw"] == "неделя 48/2006" and info["sw"] == "неделя 7/2008"
    assert info["diag_variant"] == "0x0353" and info["produced"] == "2008-07-01"
    assert find_ecu("can", 0x7E1, 0x7E9)["short"] == "EGS"
    assert find_ecu("can", 0x999, part_number="A 639 545 02 01 (?)")["short"] == "SAM"
    assert find_ecu("can", 0x999) is None
    assert Module("can", 0x5B4, 0x4F4).title == "Приборная панель (Kombi)"
    assert Module("can", 0x736, 0x4F5).title.endswith("(не подтверждено)")


def test_dtc_details(tmp_path):
    link, fake = make_link(tmp_path)
    m = Module("can", 0x7E0, 0x7E8, protocol="kwp")
    link.read_dtcs(m)
    link.read_dtc_details(m)
    p0401, p2034 = m.dtcs
    assert p0401.extra["details_status"] == "0xE0" and p0401.extra["details_env"] == "12 34"
    assert "NRC 0x11" in p2034.extra["details_error"]
    assert "172034" in fake.sent
    link.close()


def test_live_dump_and_record(tmp_path):
    from vito_diag.livedata import changing_bytes, dump_lids, record

    link, _ = make_link(tmp_path)
    m = Module("can", 0x7E0, 0x7E8, protocol="kwp")
    found = dump_lids(link, m, [0x01, 0x02])
    assert list(found) == [0x01] and found[0x01].startswith("61 01 00")
    t = iter(range(100))
    rows = record(link, m, [0x01], 5, tmp_path / "rec.csv", clock=lambda: next(t))
    assert len(rows) == 2 and (tmp_path / "rec.csv").read_text().startswith("t,lid,data")
    assert [(c["lid"], c["index"]) for c in changing_bytes(rows)] == [(0x01, 3)]
    link.close()


def test_gui_modules():
    from vito_diag.ecus import gui_modules

    mods = {m["button"]: m["can"] for m in gui_modules()}
    assert mods["Коробка (АКПП)"] == "7E1:7E9" and mods["ESP / ABS"] == "784:785"
    assert len(mods) == 11
