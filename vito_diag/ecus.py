"""Справочник блоков W639 и разбор идентификации Mercedes (1A86)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional


@lru_cache(maxsize=1)
def _catalog() -> dict:
    path = Path(__file__).parent / "data" / "ecus_w639.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _digits(part: str) -> str:
    return "".join(c for c in part if c.isdigit())


def find_ecu(bus: str, address: int, reply: int = 0, line: str = "7",
             part_number: str = "") -> Optional[dict]:
    """Запись справочника для блока: сначала по номеру детали, потом по адресу."""
    entries = _catalog()["modules"]
    digits = _digits(part_number)
    if len(digits) == 10:
        for e in entries:
            if e.get("part") == digits:
                return e
    for e in entries:
        if e["bus"] != bus:
            continue
        if bus == "can":
            if int(e["request"], 16) == address and (not reply or int(e["reply"], 16) == reply):
                return e
        elif int(e["address"], 16) == address and e.get("line", "7") == str(line):
            return e
    return None


def ecu_title(entry: Optional[dict]) -> str:
    """Название блока с пометкой, если назначение не подтверждено."""
    if not entry:
        return ""
    return entry["name"] + ("" if entry["confidence"] == "high" else " (не подтверждено)")


def supplier_name(code: int) -> str:
    return _catalog().get("suppliers", {}).get(f"0x{code:02X}", "")


def _bcd(b: int) -> Optional[int]:
    hi, lo = b >> 4, b & 0x0F
    return hi * 10 + lo if hi < 10 and lo < 10 else None


def _week_year(w: int, y: int) -> str:
    week, year = _bcd(w), _bcd(y)
    if week is None or year is None or not 1 <= week <= 53:
        return ""
    return f"неделя {week}/20{year:02d}"


def decode_1a86(payload: List[int]) -> Dict[str, str]:
    """Поля ответа 5A 86 (формат по OpenVehicleDiag read_ecu_identification).

    Байты: 2–6 номер детали BCD, 7–8 неделя/год железа, 9–10 неделя/год ПО,
    11 поставщик, 12–13 вариант диагностики, 15–17 дата выпуска (гг мм дд).
    Часть кузовных блоков W639 отдаёт этот же ответ на 1A87 и 1A90.
    """
    if len(payload) < 14 or payload[:2] != [0x5A, 0x86]:
        return {}
    out: Dict[str, str] = {}
    part = "".join(f"{b:02X}" for b in payload[2:7])
    if part.isdigit():
        out["part"] = f"A {part[:3]} {part[3:6]} {part[6:8]} {part[8:]}"
    hw = _week_year(payload[7], payload[8])
    sw = _week_year(payload[9], payload[10])
    if hw:
        out["hw"] = hw
    if sw:
        out["sw"] = sw
    sup = supplier_name(payload[11])
    out["supplier"] = f"0x{payload[11]:02X}" + (f" ({sup})" if sup else "")
    out["diag_variant"] = f"0x{payload[12]:02X}{payload[13]:02X}"
    if len(payload) >= 18:
        y, m, d = (_bcd(b) for b in payload[15:18])
        if None not in (y, m, d) and 1 <= m <= 12 and 1 <= d <= 31:
            out["produced"] = f"20{y:02d}-{m:02d}-{d:02d}"
    return out
