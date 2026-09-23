"""Чтение ошибок напрямую из блоков по UDS (ISO 14229) через ELM327, шина CAN.

Режим OBD-II видит в основном двигатель. Блоки КПП, ABS/ESP, раздатки 4MATIC,
SAM и т.д. хранят свои ошибки отдельно, и доступ к ним — по протоколу
производителя. Здесь реализован стандартный запрос UDS 0x19 0x02
(ReadDTCInformation / reportDTCByStatusMask), на который отвечают блоки,
поддерживающие UDS на диагностическом CAN (в основном Vito W447, 2014+).
Vito W639 (2003-2014) часто использует KWP2000 — там запрос может не сработать.

Адреса блоков (CAN ID) у Mercedes не публикуются: известны только стандартные
0x7E0/0x7E8 (двигатель) и обычно 0x7E1/0x7E9 (КПП). Остальные можно найти
командой `uds --scan`.
"""

import time
from typing import List, Optional, Tuple

from vito_diag.dtc import DTC, bytes_to_dtc, lookup

KNOWN_ECUS = {
    "engine": (0x7E0, 0x7E8, "Двигатель (ЭБУ)"),
    "gearbox": (0x7E1, 0x7E9, "КПП"),
}

# Биты статуса DTC по ISO 14229.
STATUS_BITS = [
    "testFailed", "testFailedThisOperationCycle", "pendingDTC", "confirmedDTC",
    "testNotCompletedSinceLastClear", "testFailedSinceLastClear",
    "testNotCompletedThisOperationCycle", "warningIndicatorRequested",
]


class ElmError(Exception):
    pass


def parse_elm_response(text: str) -> List[int]:
    """Разбирает ответ ELM327 (заголовки выкл., CAF вкл.) в байты полезной нагрузки.

    Одиночный кадр:   "59 02 FF 01 23 45 08"
    Многокадровый:    "010\\r0: 59 02 FF 01 23 45\\r1: 08 ..."
    """
    lines = [l.strip() for l in text.replace("\r", "\n").split("\n")]
    lines = [l for l in lines if l and l != ">" and not l.upper().startswith("SEARCHING")]
    for bad in ("NO DATA", "CAN ERROR", "BUS INIT", "UNABLE TO CONNECT", "STOPPED", "?", "BUFFER FULL"):
        if any(l.upper().startswith(bad) for l in lines):
            raise ElmError(lines[0] if len(lines) == 1 else " / ".join(lines))

    multi = any(":" in l for l in lines)
    if not multi:
        return [int(x, 16) for x in " ".join(lines).split()]

    total = None
    data: List[int] = []
    for l in lines:
        if ":" in l:
            _, payload = l.split(":", 1)
            data.extend(int(x, 16) for x in payload.split())
        elif total is None:
            total = int(l.replace(" ", ""), 16)
    return data[:total] if total else data


def parse_dtc_report(payload: List[int]) -> List[Tuple[str, int, int]]:
    """Ответ 59 02 <маска> [DTC_hi DTC_mid DTC_lo статус]... -> [(код, тип_сбоя, статус)]."""
    if len(payload) >= 3 and payload[0] == 0x7F:
        raise ElmError(f"Блок отклонил запрос (NRC 0x{payload[2]:02X})")
    if len(payload) < 3 or payload[0] != 0x59 or payload[1] != 0x02:
        raise ElmError("Неожиданный ответ: " + " ".join(f"{b:02X}" for b in payload))
    records = []
    body = payload[3:]
    for i in range(0, len(body) - 3, 4):
        hi, mid, lo, status = body[i:i + 4]
        if hi == mid == lo == 0:
            continue
        records.append((bytes_to_dtc(hi, mid), lo, status))
    return records


def describe_status(status: int) -> List[str]:
    return [name for bit, name in enumerate(STATUS_BITS) if status & (1 << bit)]


class ElmUds:
    """Минимальный клиент ELM327 для UDS-запросов по CAN 11 бит."""

    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 3.0):
        import serial

        self.ser = serial.serial_for_url(port, baudrate=baudrate, timeout=timeout)
        self.timeout = timeout
        for cmd in ("ATZ", "ATE0", "ATL0", "ATS1", "ATH0", "ATCAF1", "ATSP6", "ATST FF"):
            self.cmd(cmd)
        # Проверяем, что шина отвечает (стандартный запрос двигателю).
        self.cmd("0100")

    def cmd(self, command: str) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((command + "\r").encode("ascii"))
        buf = b""
        deadline = time.time() + self.timeout + 2
        while time.time() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            buf += chunk
            if b">" in buf:
                break
        return buf.decode("ascii", errors="ignore").replace(">", "").strip()

    def request(self, tx_id: int, rx_id: int, service: bytes) -> List[int]:
        self.cmd(f"ATSH{tx_id:03X}")
        self.cmd(f"ATCRA{rx_id:03X}")
        # Flow control для многокадровых ответов (поддерживают не все клоны ELM).
        self.cmd(f"ATFCSH{tx_id:03X}")
        self.cmd("ATFCSD300000")
        self.cmd("ATFCSM1")
        return parse_elm_response(self.cmd(service.hex().upper()))

    def read_dtcs(self, tx_id: int, rx_id: int, ecu_name: str = "") -> List[DTC]:
        payload = self.request(tx_id, rx_id, bytes([0x19, 0x02, 0xFF]))
        result = []
        for code, fail_type, status in parse_dtc_report(payload):
            d = lookup(code, source="uds")
            d.ecu = ecu_name or f"0x{tx_id:03X}"
            d.extra = {"failure_type": f"0x{fail_type:02X}", "status": describe_status(status)}
            result.append(d)
        return result

    def scan(self, start: int = 0x700, end: int = 0x7F7, progress=None) -> List[Tuple[int, int]]:
        """Перебор адресов: шлём TesterPresent (3E 00) и смотрим, кто ответил.

        Возвращает пары (tx_id, rx_id). Занимает 1-3 минуты.
        """
        found = []
        self.cmd("ATH1")   # показывать CAN ID ответившего
        self.cmd("ATAR")   # принимать любые ответы
        self.cmd("ATST 19")  # короткий таймаут ~100 мс
        try:
            for tx in range(start, end + 1):
                if tx == 0x7DF:  # широковещательный адрес OBD — пропускаем
                    continue
                if progress:
                    progress(tx)
                self.cmd(f"ATSH{tx:03X}")
                reply = self.cmd("3E00")
                for line in reply.replace("\r", "\n").split("\n"):
                    parts = line.split()
                    # "7E8 02 7E 00" — положительный ответ, "7E8 03 7F 3E 11" — тоже живой блок
                    if len(parts) >= 3 and len(parts[0]) == 3 and parts[2] in ("7E", "7F"):
                        found.append((tx, int(parts[0], 16)))
        finally:
            self.cmd("ATH0")
            self.cmd("ATST FF")
        return found

    def close(self):
        try:
            self.cmd("ATD")
        finally:
            self.ser.close()
