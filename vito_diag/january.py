"""Блоки двигателя ВАЗ «Январь» / Bosch M7.9.7 / М73 / М74 по K-line (KWP2000), только чтение.

Блок отвечает на адрес 0x10 (тестер 0xF1), быстрая инициализация 10400 бод, контакт 7 OBD.
Что читаем:
- идентификацию (1A 80, затем стандартные 1A 8x) — из текста берём VIN и номер блока
  вида 2112-1411020-xx; раскладку полей не угадываем, сырой ответ сохраняем;
- ошибки (18 00 FF 00, затем 18 02 FF 00);
- живые данные стандартными запросами OBD-II (служба 01: ОЖ, коррекции, лямбда, ДМРВ…),
  их понимают блоки под Евро-2/3 с лямбда-зондом; плюс сырой блок 21 01 для разбора позже.

Ничего не пишет и не стирает: все запросы проходят через защиту ElmLink (только чтение).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from vito_diag.dtc import lookup
from vito_diag.elm import Module
from vito_diag.protocol import (DiagError, check_positive, describe_kwp_status, extract_text,
                                hex_bytes, parse_kwp_dtc_report)

ADDRESSES = (0x10, 0x11)

_VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
_ECU_RE = re.compile(r"\d{4,5}-\d{7}-\d{2,3}")

# PID службы 01 (SAE J1979): имя, подпись, единицы, формула от байтов A, B.
PIDS: Dict[int, Tuple[str, str, str, Callable[[List[int]], float]]] = {
    0x04: ("LOAD", "Нагрузка", "%", lambda d: d[0] * 100 / 255),
    0x05: ("ECT", "Температура ОЖ", "°C", lambda d: d[0] - 40),
    0x06: ("STFT", "Кратковременная коррекция топлива", "%", lambda d: (d[0] - 128) * 100 / 128),
    0x07: ("LTFT", "Долговременная коррекция топлива", "%", lambda d: (d[0] - 128) * 100 / 128),
    0x0B: ("MAP", "Давление во впуске", "кПа", lambda d: d[0]),
    0x0C: ("RPM", "Обороты", "об/мин", lambda d: (d[0] * 256 + d[1]) / 4),
    0x0D: ("SPEED", "Скорость", "км/ч", lambda d: d[0]),
    0x0E: ("TIMING", "Угол опережения зажигания", "°", lambda d: d[0] / 2 - 64),
    0x0F: ("IAT", "Температура воздуха", "°C", lambda d: d[0] - 40),
    0x10: ("MAF", "Расход воздуха (ДМРВ)", "кг/ч", lambda d: (d[0] * 256 + d[1]) / 100 * 3.6),
    0x11: ("TPS", "Положение дросселя", "%", lambda d: d[0] * 100 / 255),
    0x14: ("O2S1", "Лямбда-зонд 1 (до катализатора)", "В", lambda d: d[0] / 200),
    0x15: ("O2S2", "Лямбда-зонд 2 (после катализатора)", "В", lambda d: d[0] / 200),
    0x1F: ("RUNTIME", "Время работы с запуска", "с", lambda d: d[0] * 256 + d[1]),
    0x42: ("VOLT", "Напряжение бортсети (ЭБУ)", "В", lambda d: (d[0] * 256 + d[1]) / 1000),
}

FUEL_SYSTEM = {1: "разомкнутый контур (не прогрет)", 2: "работа по лямбда-зонду",
               4: "разомкнутый контур (нагрузка/торможение)", 8: "разомкнутый контур (неисправность)",
               16: "по лямбда-зонду, но с неисправностью датчика"}


@dataclass
class LadaReport:
    address: int = 0
    ident_raw: Dict[str, str] = field(default_factory=dict)
    ident_text: str = ""
    vin: str = ""
    ecu_number: str = ""
    dtcs: list = field(default_factory=list)
    dtc_error: str = ""
    supported_pids: List[int] = field(default_factory=list)
    samples: List[Dict[str, float]] = field(default_factory=list)
    fuel_system: str = ""
    raw_2101: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "address": f"0x{self.address:02X}", "ident_raw": self.ident_raw,
            "ident_text": self.ident_text, "vin": self.vin, "ecu_number": self.ecu_number,
            "dtcs": [{"code": d.code, "description": d.description, **d.extra} for d in self.dtcs],
            "dtc_error": self.dtc_error,
            "supported_pids": [f"{p:02X}" for p in self.supported_pids],
            "fuel_system": self.fuel_system, "samples": self.samples,
            "raw_2101": self.raw_2101, "notes": self.notes,
        }


def _request(link, module: Module, req: bytes) -> List[int]:
    payload = link.request(module, req)
    return check_positive(payload, req[0])


def connect(link, addresses=ADDRESSES) -> Optional[Module]:
    """Находит блок: быстрая инициализация, затем 5-бод. None — никто не ответил."""
    for addr in addresses:
        for init in ("fast", "slow"):
            if link.kline_connect(addr, init):
                return Module("kline", addr, line="7", protocol="kwp")
    return None


def identify(link, module: Module, rep: LadaReport) -> None:
    for req in ("1A80", "1A86", "1A87", "1A8A", "1A90", "1A91", "1A92", "1A94", "1A97", "1A98"):
        try:
            payload = _request(link, module, bytes.fromhex(req))
        except DiagError:
            continue
        rep.ident_raw[req] = hex_bytes(payload)
        text = extract_text(payload[2:])
        if text and text not in rep.ident_text:
            rep.ident_text = (rep.ident_text + " | " + text).strip(" |")
    joined = rep.ident_text.replace(" | ", " ")
    m = _VIN_RE.search(joined.replace(" ", ""))
    rep.vin = m.group(0) if m else ""
    m = _ECU_RE.search(joined)
    rep.ecu_number = m.group(0) if m else ""


def read_dtcs(link, module: Module, rep: LadaReport) -> None:
    errors = []
    for req in (bytes([0x18, 0x00, 0xFF, 0x00]), bytes([0x18, 0x02, 0xFF, 0x00])):
        try:
            records = parse_kwp_dtc_report(link.request(module, req))
        except DiagError as e:
            errors.append(f"{hex_bytes(req)}: {e}")
            continue
        for code, raw, status in records:
            d = lookup(code, source="ecu")
            d.extra = {"raw": raw, "status_byte": f"0x{status:02X}",
                       "status": describe_kwp_status(status)}
            rep.dtcs.append(d)
        rep.dtc_error = ""
        return
    rep.dtc_error = "; ".join(errors)


def supported_pids(link, module: Module) -> List[int]:
    """Битовые маски 01 00 / 01 20 / 01 40 -> список поддерживаемых PID."""
    pids: List[int] = []
    base = 0x00
    while base <= 0x40:
        try:
            payload = _request(link, module, bytes([0x01, base]))
        except DiagError:
            break
        mask = payload[2:6]
        if len(mask) < 4:
            break
        for i in range(32):
            if mask[i // 8] & (0x80 >> (i % 8)):
                pids.append(base + i + 1)
        if not pids or pids[-1] != base + 0x20:
            break
        base += 0x20
    return pids


def read_sample(link, module: Module, pids: List[int]) -> Dict[str, float]:
    sample: Dict[str, float] = {}
    for pid in pids:
        if pid not in PIDS:
            continue
        name, _, _, formula = PIDS[pid]
        try:
            payload = _request(link, module, bytes([0x01, pid]))
            sample[name] = round(formula(payload[2:]), 2)
        except (DiagError, IndexError):
            continue
    return sample


def read_fuel_system(link, module: Module) -> str:
    try:
        payload = _request(link, module, bytes([0x01, 0x03]))
    except DiagError:
        return ""
    return FUEL_SYSTEM.get(payload[2], f"0x{payload[2]:02X}") if len(payload) > 2 else ""


def read_2101(link, module: Module) -> str:
    try:
        return hex_bytes(_request(link, module, bytes([0x21, 0x01])))
    except DiagError as e:
        return f"ERR {e}"


def check_mixture(samples: List[Dict[str, float]], fuel_system: str = "") -> List[str]:
    """Подсказки по переливу / бедной смеси по записанным живым данным (двигатель работает)."""
    running = [s for s in samples if s.get("RPM", 0) > 400]
    if not running:
        return ["Двигатель не работал во время записи: коррекции, лямбда и ДМРВ "
                "оцениваются только на заведённом прогретом моторе."]
    out = []

    def values(name):
        return [s[name] for s in running if name in s]

    def avg(name):
        v = values(name)
        return sum(v) / len(v) if v else None

    ect = avg("ECT")
    if ect is not None:
        if ect <= -35 or ect >= 135:
            out.append(f"ОЖ {ect:.0f} °C — нереальное значение: обрыв или замыкание датчика температуры ОЖ.")
        elif ect < 70:
            out.append(f"ОЖ всего {ect:.0f} °C: блок считает мотор холодным и обогащает смесь. Если "
                       "мотор на самом деле тёплый (стрелка в норме) — врёт датчик ОЖ; если холодный — термостат.")
    ltft, stft = avg("LTFT"), avg("STFT")
    if ltft is not None:
        total = ltft + (stft or 0)
        if total < -10:
            out.append(f"Коррекции {total:+.0f} % (долговременная {ltft:+.0f} %): блок сам убирает топливо, "
                       "значит лишнее топливо приходит помимо расчёта — подтекающие форсунки, завышенное "
                       "давление (регулятор), клапан адсорбера, бензин в масле, или ДМРВ завышает расход воздуха.")
        elif total > 10:
            out.append(f"Коррекции {total:+.0f} %: смесь бедная — подсос воздуха, слабый насос/фильтр, "
                       "грязные форсунки или ДМРВ занижает.")
    if fuel_system and "разомкнутый" in fuel_system and ect is not None and ect >= 70:
        out.append(f"Прогретый мотор, но топливная система: {fuel_system}. Лямбда-зонд не используется — "
                   "проверить зонд, его подогрев и проводку.")
    o2 = values("O2S1")
    if len(o2) >= 5:
        if min(o2) > 0.6:
            out.append(f"Лямбда-зонд всё время {min(o2):.2f}–{max(o2):.2f} В — смесь постоянно богатая.")
        elif max(o2) < 0.3:
            out.append(f"Лямбда-зонд всё время {min(o2):.2f}–{max(o2):.2f} В — смесь бедная или зонд мёртв.")
        elif max(o2) - min(o2) < 0.4:
            out.append(f"Лямбда-зонд почти не переключается ({min(o2):.2f}–{max(o2):.2f} В) — ленивый зонд.")
    idle = [s for s in running if s.get("RPM", 0) < 1100 and s.get("TPS", 0) < 3]
    maf = [s["MAF"] for s in idle if "MAF" in s]
    if maf:
        m = sum(maf) / len(maf)
        if m > 14:
            out.append(f"На холостом ДМРВ показывает {m:.1f} кг/ч (для 1,5–1,6 л обычно 7–12): датчик может "
                       "завышать расход, и блок льёт больше топлива.")
    tps = [s["TPS"] for s in running if "TPS" in s]
    if tps and min(tps) > 5:
        out.append(f"Дроссель не ниже {min(tps):.0f} % даже на холостом — проверить ДПДЗ и упор дросселя.")
    if not out:
        out.append("По живым данным явных причин перелива не видно: проверить давление топлива "
                   "манометром и форсунки на пролив/подтекание.")
    return out


def scan(link, live_count: int = 10, progress: Optional[Callable[[str], None]] = None,
         addresses=ADDRESSES) -> LadaReport:
    say = progress or (lambda _t: None)
    rep = LadaReport()
    module = connect(link, addresses)
    if module is None:
        raise DiagError("Блок не отвечает на K-line по адресам " +
                        ", ".join(f"0x{a:02X}" for a in addresses))
    rep.address = module.address
    say("опознание")
    identify(link, module, rep)
    say("ошибки")
    read_dtcs(link, module, rep)
    say("живые данные")
    rep.supported_pids = supported_pids(link, module)
    if not rep.supported_pids:
        rep.notes.append("Блок не отвечает на запросы OBD-II (служба 01) — вероятно, прошивка Евро-2 "
                         "без лямбда-зонда; живые данные только сырым блоком 21 01.")
    rep.fuel_system = read_fuel_system(link, module) if 0x03 in rep.supported_pids else ""
    for i in range(live_count):
        say(f"живые данные {i + 1}/{live_count}")
        if rep.supported_pids:
            rep.samples.append(read_sample(link, module, rep.supported_pids))
        rep.raw_2101.append(read_2101(link, module))
    return rep
