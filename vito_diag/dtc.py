"""Коды неисправностей (DTC): расшифровка байтов и база описаний."""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SYSTEM_BY_LETTER = {
    "P": "Силовой агрегат (двигатель, КПП)",
    "C": "Шасси (ABS, ESP, подвеска, 4MATIC)",
    "B": "Кузов (SAM, свет, климат, подушки)",
    "U": "Сеть обмена данными (CAN)",
}

# Третий символ кода P0xxx/P1xxx — подсистема (SAE J2012).
P_SUBSYSTEM = {
    "0": "Топливо/воздух и контроль выбросов",
    "1": "Дозирование топлива и воздуха",
    "2": "Дозирование топлива и воздуха (цепи форсунок)",
    "3": "Система зажигания / пропуски воспламенения, свечи накала",
    "4": "Дополнительные системы контроля выбросов (EGR, DPF)",
    "5": "Скорость автомобиля, холостой ход, вспомогательные входы",
    "6": "Блок управления и выходные цепи",
    "7": "Трансмиссия",
    "8": "Трансмиссия",
    "9": "Трансмиссия",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
SEVERITY_RU = {
    "critical": "КРИТИЧНО",
    "high": "Высокая",
    "medium": "Средняя",
    "low": "Низкая",
    "unknown": "Неизвестно",
}

GROUP_RU = {
    "engine": "Двигатель", "fuel": "Топливная система", "air": "Впуск воздуха",
    "turbo": "Турбонаддув", "egr": "Рециркуляция ОГ (EGR)",
    "dpf": "Сажевый фильтр (DPF)", "scr": "SCR / AdBlue", "glow": "Свечи накала",
    "cooling": "Охлаждение", "electrical": "Электропитание",
    "network": "Электроника / CAN", "transmission": "КПП",
    "chassis": "Шасси", "other": "Прочее",
}


def bytes_to_dtc(b1: int, b2: int) -> str:
    """Два байта OBD/UDS -> строка вида P0123 (SAE J2012)."""
    letter = "PCBU"[(b1 >> 6) & 0x03]
    return f"{letter}{(b1 >> 4) & 0x03}{b1 & 0x0F:X}{b2:02X}"


def is_manufacturer_code(code: str) -> bool:
    """P1xxx/P3xxx, C1/C2, B1/B2, U1/U2 — коды, определяемые производителем."""
    if len(code) < 2:
        return False
    if code[0] == "P":
        return code[1] in "13"
    return code[1] in "12"


@dataclass
class DTC:
    code: str
    description: str = ""
    severity: str = "unknown"
    advice: str = ""
    group: str = "other"
    source: str = "stored"  # stored / pending / ecu
    ecu: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def system(self) -> str:
        return SYSTEM_BY_LETTER.get(self.code[:1], "Неизвестная система")

    @property
    def is_manufacturer_specific(self) -> bool:
        return is_manufacturer_code(self.code)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "description": self.description,
            "severity": self.severity,
            "advice": self.advice,
            "group": self.group,
            "system": self.system,
            "source": self.source,
            "ecu": self.ecu,
            "manufacturer_specific": self.is_manufacturer_specific,
            **({"extra": self.extra} if self.extra else {}),
        }


@lru_cache(maxsize=1)
def load_database() -> dict:
    path = Path(__file__).parent / "data" / "dtc_ru.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def generic_description(code: str) -> str:
    """Общая расшифровка кода, которого нет в базе."""
    code = code.upper()
    parts = [SYSTEM_BY_LETTER.get(code[:1], "Неизвестная система")]
    if code[:1] == "P" and code[1:2] in ("0", "1") and len(code) >= 3:
        parts.append(P_SUBSYSTEM.get(code[2], ""))
    if is_manufacturer_code(code):
        parts.append("код производителя (Mercedes) — точная расшифровка в Xentry/DAS")
    return "; ".join(p for p in parts if p)


def lookup(code: str, source: str = "stored", ecu_description: str = "") -> DTC:
    code = code.strip().upper()
    base = code[:5]
    entry = load_database().get(base)
    if entry:
        return DTC(code=code, source=source, **entry)
    group = "transmission" if base[:2] in ("P0", "P1") and base[2:3] in "789" else "other"
    if base[:1] == "U":
        group = "network"
    elif base[:1] == "C":
        group = "chassis"
    desc = ecu_description or generic_description(base)
    return DTC(code=code, description=desc, group=group, source=source,
               advice="Код отсутствует в локальной базе — уточните по сервисной документации.")
