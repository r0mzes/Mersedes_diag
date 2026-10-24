"""Генерирует файлы прошивки приборки из исходников vito_diag (один источник правды).

    python tools/gen_panel_assets.py

firmware/esp32_obd_panel/panel_html.h — страница vito_diag/data/panel.html;
firmware/esp32_obd_panel/dtc_table.h  — описания кодов из vito_diag/data/dtc_ru.json.
Тест tests/test_panel_firmware.py проверяет, что файлы не устарели.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "firmware" / "esp32_obd_panel"


def c_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # экранирование JSON подходит и для C++


def panel_html() -> str:
    html = (ROOT / "vito_diag" / "data" / "panel.html").read_text(encoding="utf-8")
    assert ")PANEL\"" not in html
    return ("// Сгенерировано tools/gen_panel_assets.py из vito_diag/data/panel.html — не править руками.\n"
            "#pragma once\n#ifndef PROGMEM\n#define PROGMEM\n#endif\n"
            f'static const char PANEL_HTML[] PROGMEM = R"PANEL({html})PANEL";\n')


def dtc_table() -> str:
    sys.path.insert(0, str(ROOT))
    from vito_diag.dtc import P_SUBSYSTEM

    db = json.loads((ROOT / "vito_diag" / "data" / "dtc_ru.json").read_text(encoding="utf-8"))
    rows = "\n".join(f"    {{{c_str(code)}, {c_str(e['description'])}}}," for code, e in sorted(db.items()))
    subs = ", ".join(c_str(P_SUBSYSTEM.get(str(i), "")) for i in range(10))
    return ("// Сгенерировано tools/gen_panel_assets.py из vito_diag/data/dtc_ru.json — не править руками.\n"
            "#pragma once\n\nstruct DtcEntry {\n  const char *code, *description;\n};\n\n"
            f"static const DtcEntry DTC_TABLE[] = {{\n{rows}\n}};\n\n"
            f"static const char *const P_SUBSYSTEM[10] = {{{subs}}};\n")


FILES = {"panel_html.h": panel_html, "dtc_table.h": dtc_table}


def main(check: bool = False) -> int:
    stale = []
    for name, make in FILES.items():
        text = make()
        path = OUT / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"записан {path.relative_to(ROOT)}")
    if stale:
        print("устарели: " + ", ".join(stale) + " — запустите python tools/gen_panel_assets.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
