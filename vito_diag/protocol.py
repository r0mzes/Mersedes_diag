"""Разбор ответов ELM327 и диагностических протоколов KWP2000 / UDS.

Здесь только чистые функции без обращения к железу — их легко тестировать.
"""

import re
from typing import List, Optional, Tuple

from vito_diag.dtc import bytes_to_dtc

ELM_ERRORS = ("NO DATA", "CAN ERROR", "BUS INIT", "BUS ERROR", "UNABLE TO CONNECT",
              "STOPPED", "?", "BUFFER FULL", "FB ERROR", "DATA ERROR", "ACT ALERT", "LV RESET")

# Коды отрицательных ответов (NRC), общие для KWP2000 и UDS.
NRC = {
    0x10: "общий отказ", 0x11: "сервис не поддерживается",
    0x12: "подфункция не поддерживается", 0x13: "неверная длина/формат",
    0x21: "занят, повторите", 0x22: "условия не выполнены",
    0x31: "параметр вне диапазона", 0x33: "доступ запрещён (нужен ключ)",
    0x78: "ответ будет позже", 0x7E: "подфункция не поддерживается в этой сессии",
    0x7F: "сервис не поддерживается в этой сессии",
}

# Сервисы, которые только ЧИТАЮТ данные. Всё остальное (стирание, запись,
# сброс блоков, управление исполнителями, прошивка) программа не отправит
# без явного флага --unsafe.
READ_ONLY_SERVICES = {
    0x01, 0x02, 0x03, 0x05, 0x06, 0x07, 0x09, 0x0A,  # OBD-II (без 04 — стирание, 08 — управление)
    0x12,  # KWP: стоп-кадр
    0x17, 0x18,  # KWP: чтение ошибок
    0x19,  # UDS: чтение ошибок
    0x1A,  # KWP: идентификация блока
    0x21, 0x22,  # чтение данных по локальному ID / DID
    0x23,  # чтение памяти
    0x3E,  # TesterPresent
}


class DiagError(Exception):
    """Ошибка связи или отрицательный ответ блока."""

    def __init__(self, message: str, nrc: Optional[int] = None):
        super().__init__(message)
        self.nrc = nrc


def is_read_only(request: bytes) -> bool:
    if not request:
        return False
    service = request[0]
    if service == 0x10:  # сессия: разрешаем только штатную (KWP 0x81 / UDS 0x01)
        return len(request) > 1 and request[1] in (0x01, 0x81)
    return service in READ_ONLY_SERVICES


def hex_bytes(data) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_hex_request(text: str) -> bytes:
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", text)
    if not cleaned or len(cleaned) % 2:
        raise ValueError(f"Неверный запрос: {text!r} (нужны пары hex-цифр, например '1A 86')")
    return bytes.fromhex(cleaned)


def _clean_lines(text: str) -> List[str]:
    lines = [l.strip() for l in text.replace("\r", "\n").split("\n")]
    lines = [l for l in lines if l and l != ">" and not l.upper().startswith("SEARCHING")]
    for l in lines:
        if any(l.upper().startswith(e) for e in ELM_ERRORS):
            raise DiagError(" / ".join(lines))
    return lines


def parse_can_response(text: str) -> List[int]:
    """Ответ ELM327 на CAN (заголовки выкл., CAF вкл.) -> байты полезной нагрузки.

    Одиночный кадр:   "59 02 FF 01 23 45 08"
    Многокадровый:    "00B\\r0: 59 02 FF 01 23 45\\r1: 08 ..."
    """
    lines = _clean_lines(text)
    if not any(":" in l for l in lines):
        # Несколько одиночных кадров подряд (например, 7F xx 78, затем ответ) —
        # берём последний.
        return [int(x, 16) for x in lines[-1].split()] if lines else []
    total = None
    data: List[int] = []
    for l in lines:
        if ":" in l:
            data.extend(int(x, 16) for x in l.split(":", 1)[1].split())
        elif total is None:
            total = int(l.replace(" ", ""), 16)
    return data[:total] if total else data


def parse_kline_response(text: str) -> List[List[int]]:
    """Ответ ELM327 на K-line (заголовки ВКЛ.) -> список сообщений (только данные).

    Формат KWP2000: <fmt> [<tgt> <src>] [<len>] <данные...> <контрольная сумма>
    """
    messages = []
    for line in _clean_lines(text):
        try:
            b = [int(x, 16) for x in line.split()]
        except ValueError:
            continue
        if not b:
            continue
        fmt, idx = b[0], 1
        if fmt & 0x80:  # есть адреса
            idx += 2
        length = fmt & 0x3F
        if length == 0 and len(b) > idx:
            length = b[idx]
            idx += 1
        data = b[idx:idx + length] if length else b[idx:-1]
        messages.append(data)
    return messages


def final_payload(messages: List[List[int]], service: int) -> List[int]:
    """Выбирает итоговый ответ, пропуская «ответ будет позже» (7F xx 78)."""
    for m in reversed(messages):
        if len(m) >= 3 and m[0] == 0x7F and m[2] == 0x78:
            continue
        if m and (m[0] == service + 0x40 or m[0] == 0x7F):
            return m
    return messages[-1] if messages else []


def check_positive(payload: List[int], service: int) -> List[int]:
    if len(payload) >= 3 and payload[0] == 0x7F:
        nrc = payload[2]
        raise DiagError(f"Блок отклонил запрос 0x{service:02X}: NRC 0x{nrc:02X} "
                        f"({NRC.get(nrc, 'неизвестно')})", nrc=nrc)
    if not payload or payload[0] != service + 0x40:
        raise DiagError("Неожиданный ответ: " + hex_bytes(payload))
    return payload


def parse_uds_dtc_report(payload: List[int]) -> List[Tuple[str, str, int]]:
    """UDS 59 02 <маска> [hi mid lo статус]... -> [(код, сырой hex, статус)]."""
    check_positive(payload, 0x19)
    records = []
    body = payload[3:]
    for i in range(0, len(body) - 3, 4):
        hi, mid, lo, status = body[i:i + 4]
        if hi == mid == lo == 0:
            continue
        records.append((bytes_to_dtc(hi, mid), f"{hi:02X}{mid:02X}{lo:02X}", status))
    return records


def parse_kwp_dtc_report(payload: List[int]) -> List[Tuple[str, str, int]]:
    """KWP2000 58 <кол-во> [hi lo статус]... -> [(код, сырой hex, статус)]."""
    check_positive(payload, 0x18)
    records = []
    body = payload[2:]
    for i in range(0, len(body) - 2, 3):
        hi, lo, status = body[i:i + 3]
        if hi == lo == 0:
            continue
        records.append((bytes_to_dtc(hi, lo), f"{hi:02X}{lo:02X}", status))
    return records


UDS_STATUS_BITS = [
    "testFailed", "testFailedThisOperationCycle", "pending", "confirmed",
    "testNotCompletedSinceLastClear", "testFailedSinceLastClear",
    "testNotCompletedThisOperationCycle", "warningIndicatorRequested",
]


def describe_uds_status(status: int) -> List[str]:
    return [n for bit, n in enumerate(UDS_STATUS_BITS) if status & (1 << bit)]


def describe_kwp_status(status: int) -> List[str]:
    # ISO 14230-3: биты 5-6 — хранение, бит 7 — лампа.
    storage = {0: "нет", 1: "текущая (не подтверждена)", 2: "сохранена, сейчас нет",
               3: "сохранена и присутствует"}[(status >> 5) & 0x03]
    out = [f"состояние: {storage}"]
    if status & 0x80:
        out.append("горит лампа")
    return out


_PART_RE = re.compile(r"A?\s?(\d{3})\s?(\d{3})\s?(\d{2})\s?(\d{2})")


def extract_text(payload: List[int]) -> str:
    """Печатные ASCII-фрагменты ответа (номера деталей, VIN, версии)."""
    chars = "".join(chr(b) if 32 <= b < 127 else " " for b in payload)
    return " ".join(w for w in chars.split() if len(w) >= 4)


def extract_part_number(payload: List[int]) -> Optional[str]:
    """Номер детали Mercedes («A 000 446 12 34») из ответа идентификации.

    Mercedes часто хранит номер в BCD: 10 цифр в 5 байтах. Такая догадка
    помечается «(?)» — её надо сверить с наклейкой на блоке.
    """
    text = extract_text(payload)
    m = _PART_RE.search(text)
    if m:
        return "A " + " ".join(m.groups())
    body = payload[2:]
    for i in range(len(body) - 4):
        chunk = body[i:i + 5]
        if all((b >> 4) < 10 and (b & 0x0F) < 10 for b in chunk):
            digits = "".join(f"{b:02X}" for b in chunk)
            if digits != "0" * 10:
                return f"A {digits[:3]} {digits[3:6]} {digits[6:8]} {digits[8:]} (?)"
    return None
