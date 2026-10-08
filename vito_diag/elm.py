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
    extract_text, final_payload, hex_bytes, is_read_only, kline_source, parse_can_response_ex,
    parse_kline_response, parse_kwp_dtc_report, parse_uds_dtc_report,
)

TESTER_ADDR = 0xF1


def open_serial(port: str, factory=None, **kwargs):
    """Открыть порт, не поднимая DTR/RTS.

    Мост на ESP32 (docs/ESP32_BRIDGE.md) сидит на FTDI, у которого RTS/DTR заведены
    на сброс платы: при обычном открытии ESP32 перезагружается или остаётся в сбросе,
    и связь с адаптером по Bluetooth рвётся.
    """
    if factory is None:
        import serial

        factory = serial.serial_for_url
    ser = factory(port, do_not_open=True, **kwargs)
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


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
    notes: List[str] = field(default_factory=list)  # оговорки: обрезанные ответы и т. п.

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
            "ident_raw": self.ident_raw, "error": self.error, "notes": self.notes,
            "dtcs": [d.to_dict() for d in self.dtcs],
        }


class ElmLink:
    """Минимальный клиент ELM327 с журналом обмена."""

    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 3.0,
                 log_dir: str = "logs", unsafe: bool = False, ser=None):
        if ser is None:
            ser = open_serial(port, baudrate=baudrate, timeout=0.1)
        self.ser = ser
        self.timeout = timeout
        self.unsafe = unsafe
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_dir) / f"elm_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
        self._log = open(self.log_path, "a", encoding="utf-8")
        self.mode = None      # "can" / "kline"
        self.target = None
        # Особенности клонов ELM327 (выясняются по ходу работы):
        self.can_header_ignored = None  # True — ATSH на CAN не действует (запросы уходят всем)
        self.no_fast_init = False       # ATFI не поддерживается — K-line только автоинициализацией
        self.no_slow_init = False       # ATSI не поддерживается — то же для 5-бод инициализации
        self.kline_header_ignored = False  # на K-line отвечает один блок, какой адрес ни задай
        self.kline_answered: Dict[int, set] = {}  # адрес ответившего -> адреса, к которым обращались
        self.truncated = False          # последний ответ на CAN обрезан (нет flow control)
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
        data, total = parse_can_response_ex(self.cmd(request.hex().upper()))
        self.truncated = bool(total) and len(data) < total
        if self.truncated:
            self.log(f"!! ответ обрезан: получено {len(data)} из {total} байт")
        return final_payload([data], request[0])

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

    def _probe_can(self, tx: int) -> List[int]:
        """TesterPresent на CAN ID tx -> CAN ID ответивших блоков (нужен ATH1)."""
        self.cmd(f"ATSH{tx:03X}")
        for probe in ("3E00", "3E01"):
            replies = set()
            for line in self.cmd(probe).replace("\r", "\n").split("\n"):
                p = line.split()
                # "7E8 02 7E 00" — ответ; "7E8 03 7F 3E 11" — отказ, но блок живой
                if len(p) >= 3 and len(p[0]) == 3 and p[2] in ("7E", "7F"):
                    replies.add(int(p[0], 16))
            if replies:
                return sorted(replies)
        return []

    def scan_can(self, start: int = 0x400, end: int = 0x7FF,
                 progress: Optional[Callable[[int], None]] = None) -> List[Module]:
        """Шлёт TesterPresent на каждый CAN ID и собирает ответившие блоки.

        Сначала проверяет, действует ли ATSH: если на запросы к 0x7E0 и 0x7E1 отвечают одни
        и те же блоки, адаптер игнорирует заголовок (дешёвые клоны). Тогда перебор бессмыслен
        (каждый ID «ответит»), и найти можно только блоки, отвечающие на общий OBD-запрос.
        """
        self.setup_can()
        self.cmd("ATH1")
        self.cmd("ATCRA")
        self.cmd("ATST 19")  # ~100 мс ожидания
        self.target = None
        found: Dict[int, Module] = {}
        try:
            a, b = self._probe_can(0x7E0), self._probe_can(0x7E1)
            self.can_header_ignored = bool(a) and a == b
            if self.can_header_ignored:
                self.log("!! адаптер игнорирует ATSH: перебор CAN ID пропущен")
                for rx in a:
                    # Для ответов 7E8..7EF ID запроса по стандарту OBD на 8 меньше.
                    tx = rx - 8 if 0x7E8 <= rx <= 0x7EF else 0x7DF
                    found[rx] = Module("can", tx, rx)
                return list(found.values())
            for tx in range(start, end + 1):
                if tx == 0x7DF:  # широковещательный адрес OBD
                    continue
                if progress:
                    progress(tx)
                for rx in self._probe_can(tx):
                    found.setdefault(tx, Module("can", tx, rx))
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
        self.cmd("ATST FF")    # ждать до ~1 с: длинные ответы идут частями вперемешку с 7F xx 78
        self.cmd(f"ATSH81{address:02X}{TESTER_ADDR:02X}")
        self.cmd(f"ATWM81{address:02X}{TESTER_ADDR:02X}3E")  # поддержание связи
        if init == "fast" and self.no_fast_init:
            return self._kline_auto_init(address)
        if init == "slow" and self.no_slow_init:
            self.cmd(f"ATIIA{address:02X}")  # 5-бод инициализация на первом запросе — по этому адресу
            return self._kline_auto_init(address)
        if init == "fast":
            reply = self.cmd("ATFI", wait=4)
            if reply.strip() == "?":
                # Клон без ATFI: адаптер сам сделает быструю инициализацию на первом запросе.
                self.log("!! ATFI не поддерживается — K-line через автоинициализацию")
                self.no_fast_init = True
                return self._kline_auto_init(address)
        else:
            self.cmd(f"ATIIA{address:02X}")
            reply = self.cmd("ATSI", wait=6)
            if reply.strip() == "?":
                # Клон без ATSI (проверено 2026-10-08): медленная инициализация — тоже первым запросом.
                self.log("!! ATSI не поддерживается — 5-бод инициализация первым запросом")
                self.no_slow_init = True
                return self._kline_auto_init(address)
        ok = "OK" in reply.upper() and "ERROR" not in reply.upper()
        if ok:
            self.target = address
        return ok

    def _kline_auto_init(self, address: int) -> bool:
        """Инициализация первым запросом (TesterPresent). Блок засчитывается, только если
        ответил именно он: клон может отправить инициализацию не на тот адрес."""
        reply = self.cmd("3E01", wait=6)
        try:
            src = kline_source(reply)
        except DiagError:
            return False
        if src is not None:
            self.kline_answered.setdefault(src, set()).add(address)
        if src != address:
            if src is not None:
                self.log(f"!! на запрос к 0x{address:02X} ответил 0x{src:02X}")
            return False
        self.target = address
        return True

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
        self.kline_answered = {}
        for addr in addresses:
            if addr == TESTER_ADDR:
                continue
            if progress:
                progress(addr)
            # slow="only" — только 5-бод (когда быстрая инициализация на линии уже проверена)
            ok = ((slow != "only" and self.kline_connect(addr, "fast"))
                  or (bool(slow) and self.kline_connect(addr, "slow")))
            if ok:
                found.append(Module("kline", addr, line=line, protocol="kwp"))
            # Один и тот же блок отвечает на запросы к разным адресам — адаптер игнорирует ATSH,
            # дальше перебирать бессмысленно: ответит только он.
            src = next((s for s, asked in self.kline_answered.items() if len(asked) >= 2), None)
            if src is not None:
                self.kline_header_ignored = True
                self.log(f"!! адаптер игнорирует адрес K-line: отвечает только 0x{src:02X}")
                if all(m.address != src for m in found):
                    found.append(Module("kline", src, line=line, protocol="kwp"))
                break
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
            truncated = module.bus == "can" and self.truncated
            module.ident_raw[req] = hex_bytes(payload) + (" …(обрезано)" if truncated else "")
            if not module.protocol:
                module.protocol = "kwp" if req.startswith("1A") else "uds"
            if truncated:
                # По обрывку номер детали не угадываем — будет ложный результат.
                note = "идентификация обрезана: адаптер не принимает длинные ответы"
                if note not in module.notes:
                    module.notes.append(note)
                continue
            text = extract_text(payload[2:])
            if text and text not in module.ident_text:
                module.ident_text = (module.ident_text + " | " + text).strip(" |")
            # 1A90 / 22F190 — это VIN: в его цифрах регулярка «находит» ложный номер детали.
            if not module.part_number and req not in ("1A90", "22F190"):
                module.part_number = extract_part_number(payload) or ""

    # Группы ошибок KWP2000 (старшие биты первого байта кода): P, C, B, U.
    KWP_DTC_GROUPS = (0x0000, 0x4000, 0x8000, 0xC000)

    def _read_kwp_dtcs(self, module: Module) -> list:
        """KWP 18 02 FF 00; если ответ обрезан — по группам, чтобы ответы влезали в один кадр."""
        payload = self.request(module, bytes([0x18, 0x02, 0xFF, 0x00]))
        records = parse_kwp_dtc_report(payload)
        if not (module.bus == "can" and self.truncated):
            return records
        total = payload[1]
        seen = {r[1]: r for r in records}
        for group in self.KWP_DTC_GROUPS:
            try:
                part = self.request(module, bytes([0x18, 0x02, group >> 8, group & 0xFF]))
                group_records = parse_kwp_dtc_report(part)
            except DiagError:
                continue
            for r in group_records:
                seen.setdefault(r[1], r)
            if self.truncated and len(group_records) < part[1]:
                module.notes.append(f"группа {group:04X}: ошибок {part[1]}, прочитано "
                                    f"{len(group_records)} (ответ обрезан)")
        if len(seen) < total:
            module.notes.append(f"блок сообщает ошибок: {total}, прочитано: {len(seen)}")
        return list(seen.values())

    def read_dtcs(self, module: Module) -> None:
        """Читает ошибки: сначала KWP2000 (0x18), затем UDS (0x19)."""
        order = ["uds", "kwp"] if module.protocol == "uds" else ["kwp", "uds"]
        errors = []
        for proto in order:
            try:
                if proto == "kwp":
                    records = self._read_kwp_dtcs(module)
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
