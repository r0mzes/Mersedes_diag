"""Работа с адаптером ELM327 напрямую: CAN и K-line, поиск блоков, KWP2000/UDS.

Весь обмен с адаптером пишется в лог (logs/elm_*.log) — по нему можно
разобрать ответы блоков, даже если программа их пока не понимает.

Режим OBD-II (python-OBD) видит в основном двигатель. Остальные блоки Mercedes
отвечают по своим адресам:
  * на CAN (контакты 6/14) — по KWP2000 или UDS поверх ISO-TP;
  * на K-line (контакт 7, а через переключатель — 8, 9, 11) — по KWP2000.
Адреса блоков W639 не опубликованы, поэтому их ищем перебором (scan_can/scan_kline).
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from vito_diag.dtc import DTC, lookup
from vito_diag.protocol import (
    DiagError, check_positive, describe_kwp_status, describe_uds_status, extract_part_number,
    extract_text, final_payload, hex_bytes, is_read_only, parse_can_response,
    parse_kline_response, parse_kwp_dtc_report, parse_uds_dtc_report,
)

TESTER_ADDR = 0xF1


@dataclass
class Module:
    bus: str                 # "can" или "kline"
    address: int             # CAN ID запроса или адрес K-line
    reply: int = 0           # CAN ID ответа
    line: str = "7"          # контакт OBD (для K-line через переключатель)
    protocol: str = ""       # "kwp" / "uds"
    part_number: str = ""
    ident_text: str = ""
    ident_raw: Dict[str, str] = field(default_factory=dict)
    dtcs: List[DTC] = field(default_factory=list)
    error: str = ""

    @property
    def name(self) -> str:
        if self.bus == "can":
            return f"CAN 0x{self.address:03X}/0x{self.reply:03X}"
        return f"K-line (конт. {self.line}) 0x{self.address:02X}"

    def to_dict(self) -> dict:
        return {
            "bus": self.bus, "address": f"0x{self.address:X}",
            "reply": f"0x{self.reply:X}" if self.reply else "",
            "line": self.line, "protocol": self.protocol,
            "part_number": self.part_number, "ident_text": self.ident_text,
            "ident_raw": self.ident_raw, "error": self.error,
            "dtcs": [d.to_dict() for d in self.dtcs],
        }


class ElmLink:
    """Минимальный клиент ELM327 с журналом обмена."""

    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 3.0,
                 log_dir: str = "logs", unsafe: bool = False, ser=None):
        if ser is None:
            import serial

            ser = serial.serial_for_url(port, baudrate=baudrate, timeout=0.1)
        self.ser = ser
        self.timeout = timeout
        self.unsafe = unsafe
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_dir) / f"elm_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
        self._log = open(self.log_path, "a", encoding="utf-8")
        self.mode = None      # "can" / "kline"
        self.target = None
        self.version = self.cmd("ATZ", wait=3)
        for c in ("ATE0", "ATL0", "ATS1", "ATH0"):
            self.cmd(c)

    # ---- низкий уровень -------------------------------------------------

    def log(self, text: str) -> None:
        self._log.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + " " + text + "\n")
        self._log.flush()

    def cmd(self, command: str, wait: Optional[float] = None) -> str:
        self.ser.reset_input_buffer()
        self.log(f">> {command}")
        self.ser.write((command + "\r").encode("ascii"))
        buf = b""
        deadline = time.time() + (wait or self.timeout) + 2
        while time.time() < deadline:
            buf += self.ser.read(self.ser.in_waiting or 1)
            if b">" in buf:
                break
        text = buf.decode("ascii", errors="ignore").replace(">", "").strip()
        self.log("<< " + text.replace("\r", " | "))
        return text

    def voltage(self) -> str:
        return self.cmd("ATRV")

    def close(self):
        try:
            if self.mode == "kline":
                self.cmd("ATPC")  # закрыть сессию K-line
            self.cmd("ATD")
        finally:
            self.ser.close()
            self._log.close()

    def _guard(self, request: bytes):
        if not self.unsafe and not is_read_only(request):
            raise DiagError(
                f"Запрос {hex_bytes(request)} может изменить данные в блоке и заблокирован. "
                "Программа по умолчанию только читает (см. --unsafe)."
            )

    # ---- CAN ---------------------------------------------------------------

    def setup_can(self, protocol: str = "6"):
        """protocol 6 = ISO 15765-4 CAN 11 бит 500 кбит/с."""
        if self.mode != "can":
            self.cmd("ATPC")
            self.cmd(f"ATSP{protocol}")
            self.cmd("ATCAF1")
            self.cmd("ATH0")
            self.cmd("ATST FF")
            self.mode = "can"
            self.target = None

    def can_request(self, tx: int, rx: int, request: bytes) -> List[int]:
        self._guard(request)
        self.setup_can()
        if self.target != (tx, rx):
            self.cmd(f"ATSH{tx:03X}")
            self.cmd(f"ATCRA{rx:03X}")
            # Flow control для многокадровых ответов (не все клоны ELM это умеют).
            self.cmd(f"ATFCSH{tx:03X}")
            self.cmd("ATFCSD300000")
            self.cmd("ATFCSM1")
            self.target = (tx, rx)
        return final_payload([parse_can_response(self.cmd(request.hex().upper()))], request[0])

    def monitor_can(self, seconds: float = 5.0) -> Dict[int, int]:
        """Пассивно слушает шину: {CAN ID: число кадров}. Ничего не отправляет в машину."""
        self.setup_can()
        self.cmd("ATH1")
        self.cmd("ATCRA")  # сброс фильтра
        self.target = None
        self.log(">> ATMA")
        self.ser.write(b"ATMA\r")
        end = time.time() + seconds
        buf = b""
        while time.time() < end:
            buf += self.ser.read(self.ser.in_waiting or 1)
        self.ser.write(b"\r")  # любой символ останавливает мониторинг
        time.sleep(0.3)
        buf += self.ser.read(self.ser.in_waiting or 1)
        text = buf.decode("ascii", errors="ignore")
        self.log("<< " + text.replace("\r", " | ")[:20000])
        self.cmd("ATH0")
        ids: Dict[int, int] = {}
        for line in text.replace("\r", "\n").split("\n"):
            parts = line.strip().split()
            if len(parts) >= 2 and len(parts[0]) == 3:
                try:
                    cid = int(parts[0], 16)
                except ValueError:
                    continue
                ids[cid] = ids.get(cid, 0) + 1
        return ids

    def scan_can(self, start: int = 0x400, end: int = 0x7FF,
                 progress: Optional[Callable[[int], None]] = None) -> List[Module]:
        """Шлёт TesterPresent на каждый CAN ID и собирает ответившие блоки."""
        self.setup_can()
        self.cmd("ATH1")
        self.cmd("ATCRA")
        self.cmd("ATST 19")  # ~100 мс ожидания
        self.target = None
        found: Dict[int, Module] = {}
        try:
            for tx in range(start, end + 1):
                if tx == 0x7DF:  # широковещательный адрес OBD
                    continue
                if progress:
                    progress(tx)
                self.cmd(f"ATSH{tx:03X}")
                for probe in ("3E00", "3E01"):
                    reply = self.cmd(probe)
                    hit = False
                    for line in reply.replace("\r", "\n").split("\n"):
                        p = line.split()
                        # "7E8 02 7E 00" — ответ; "7E8 03 7F 3E 11" — отказ, но блок живой
                        if len(p) >= 3 and len(p[0]) == 3 and p[2] in ("7E", "7F"):
                            rx = int(p[0], 16)
                            found.setdefault(tx, Module("can", tx, rx))
                            hit = True
                    if hit:
                        break
        finally:
            self.cmd("ATH0")
            self.cmd("ATST FF")
        return list(found.values())

    # ---- K-line ------------------------------------------------------------

    def kline_connect(self, address: int, init: str = "fast") -> bool:
        """Инициализация связи с блоком по K-line (KWP2000). init: fast / slow."""
        self.cmd("ATPC")
        self.mode = "kline"
        self.target = None
        self.cmd("ATSP5" if init == "fast" else "ATSP4")
        self.cmd("ATH1")       # нужны заголовки, чтобы отделить длину и адреса
        self.cmd("ATKW0")      # не проверять ключевые байты (у Mercedes бывают нестандартные)
        self.cmd(f"ATSH81{address:02X}{TESTER_ADDR:02X}")
        self.cmd(f"ATWM81{address:02X}{TESTER_ADDR:02X}3E")  # поддержание связи
        if init == "fast":
            reply = self.cmd("ATFI", wait=4)
        else:
            self.cmd(f"ATIIA{address:02X}")
            reply = self.cmd("ATSI", wait=6)
        ok = "OK" in reply.upper() and "ERROR" not in reply.upper()
        if ok:
            self.target = address
        return ok

    def kline_request(self, address: int, request: bytes) -> List[int]:
        self._guard(request)
        if self.mode != "kline" or self.target != address:
            if not self.kline_connect(address) and not self.kline_connect(address, "slow"):
                raise DiagError(f"Блок 0x{address:02X} не отвечает на K-line")
        text = self.cmd(request.hex().upper())
        return final_payload(parse_kline_response(text), request[0])

    def scan_kline(self, addresses=range(0x01, 0xF0), line: str = "7", slow: bool = False,
                   progress: Optional[Callable[[int], None]] = None) -> List[Module]:
        found = []
        for addr in addresses:
            if addr == TESTER_ADDR:
                continue
            if progress:
                progress(addr)
            ok = self.kline_connect(addr, "fast") or (slow and self.kline_connect(addr, "slow"))
            if ok:
                found.append(Module("kline", addr, line=line, protocol="kwp"))
        self.cmd("ATPC")
        self.mode = None
        return found

    # ---- высокий уровень: идентификация и ошибки ---------------------------

    def request(self, module: Module, request: bytes) -> List[int]:
        if module.bus == "can":
            return self.can_request(module.address, module.reply, request)
        return self.kline_request(module.address, request)

    def identify(self, module: Module) -> None:
        """Пробует стандартные запросы идентификации; сырые ответы сохраняет."""
        for req in ("1A86", "1A87", "1A90", "22F187", "22F18C", "22F190"):
            try:
                payload = self.request(module, bytes.fromhex(req))
                check_positive(payload, int(req[:2], 16))
            except DiagError:
                continue
            module.ident_raw[req] = hex_bytes(payload)
            if not module.protocol:
                module.protocol = "kwp" if req.startswith("1A") else "uds"
            text = extract_text(payload[2:])
            if text and text not in module.ident_text:
                module.ident_text = (module.ident_text + " | " + text).strip(" |")
            if not module.part_number:
                module.part_number = extract_part_number(payload) or ""

    def read_dtcs(self, module: Module) -> None:
        """Читает ошибки: сначала KWP2000 (0x18), затем UDS (0x19)."""
        order = ["uds", "kwp"] if module.protocol == "uds" else ["kwp", "uds"]
        errors = []
        for proto in order:
            try:
                if proto == "kwp":
                    records = parse_kwp_dtc_report(self.request(module, bytes([0x18, 0x02, 0xFF, 0x00])))
                    describe = describe_kwp_status
                else:
                    records = parse_uds_dtc_report(self.request(module, bytes([0x19, 0x02, 0xFF])))
                    describe = describe_uds_status
            except DiagError as e:
                errors.append(f"{proto}: {e}")
                continue
            module.protocol = module.protocol or proto
            for code, raw, status in records:
                d = lookup(code, source="ecu")
                d.ecu = module.part_number or module.name
                d.extra = {"raw": raw, "status_byte": f"0x{status:02X}", "status": describe(status)}
                module.dtcs.append(d)
            module.error = ""
            return
        module.error = "; ".join(errors)
