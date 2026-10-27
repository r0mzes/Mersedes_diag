"""Кабель KKL (VAG KKL 409.1 на FTDI): K-line напрямую, без ELM327.

ПК сам делает быструю инициализацию ISO 14230 (25 мс низкий уровень через break + 25 мс высокий)
и шлёт кадры KWP2000 на 10400 бод. Кабель слышит свою передачу (эхо) — её отбрасываем.
Интерфейс повторяет то, чем пользуются january.py и live.py у ElmLink: kline_connect, request,
cmd("ATPC"), voltage, version, log_path, mode, close.

Проверено на «Январе-5.1» 2026-10-27: оба клона ELM327 не смогли, KKL — с первой попытки.
Только чтение: request() пропускает лишь службы чтения (protocol.is_read_only); служебные
StartCommunication (81) и StopCommunication (82) шлёт только сам транспорт.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from vito_diag.protocol import DiagError, final_payload, hex_bytes, is_read_only

TESTER = 0xF1
P3_MIN = 0.06       # пауза перед новым запросом после ответа блока, с
SESSION_IDLE = 4.0  # после стольких секунд тишины блок мог закрыть связь (P3max 5 с) — переподключаемся


def frame(target: int, data: List[int]) -> bytes:
    """Кадр KWP2000 тестер -> блок: формат, адреса, [длина], данные, контрольная сумма."""
    head = [0x80 | len(data), target, TESTER] if len(data) < 64 else [0x80, target, TESTER, len(data)]
    body = head + list(data)
    return bytes(body + [sum(body) & 0xFF])


class KklLink:
    def __init__(self, port: str, ser=None, log_dir: str = "logs", baudrate: int = 10400,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        if ser is None:
            import serial

            ser = serial.Serial(port, baudrate, timeout=0.02)
        self.ser = ser
        self.clock, self.sleep = clock, sleep
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_dir) / f"kkl_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
        self._log = open(self.log_path, "a", encoding="utf-8")
        self.version = "кабель KKL (K-line напрямую, 10400 бод)"
        self.mode: Optional[str] = None
        self.target: Optional[int] = None
        self.last = 0.0
        self.has_kline = True

    def log(self, text: str) -> None:
        self._log.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + " " + text + "\n")
        self._log.flush()

    # ---- совместимость с ElmLink ------------------------------------------------
    def cmd(self, command: str, wait: Optional[float] = None) -> str:
        if command.upper() == "ATPC":
            self.stop()
            return "OK"
        return "?"

    def voltage(self) -> str:
        return "не меряет (кабель KKL)"

    # ---- обмен --------------------------------------------------------------------
    def _read(self, n: int, timeout: float) -> bytes:
        buf = b""
        end = self.clock() + timeout
        while len(buf) < n and self.clock() < end:
            buf += self.ser.read(n - len(buf))
        return buf

    def _read_frame(self, timeout: float) -> Optional[Tuple[int, List[int]]]:
        """Один кадр от блока -> (адрес отправителя, данные); None — тишина."""
        head = self._read(1, timeout)
        if not head:
            return None
        fmt = head[0]
        hdr = self._read(2 if fmt & 0x80 else 0, 0.1)
        length = fmt & 0x3F
        if length == 0:
            lb = self._read(1, 0.1)
            if not lb:
                raise DiagError("обрыв кадра: " + hex_bytes(head + hdr))
            length = lb[0]
            hdr += lb
        rest = self._read(length + 1, 0.5)
        raw = head + hdr + rest
        if len(rest) < length + 1:
            raise DiagError("обрыв кадра: " + hex_bytes(raw))
        if sum(raw[:-1]) & 0xFF != raw[-1]:
            raise DiagError("неверная контрольная сумма: " + hex_bytes(raw))
        self.log("<< " + hex_bytes(raw))
        src = hdr[1] if fmt & 0x80 else -1
        return src, list(rest[:-1])

    def _exchange(self, target: int, data: List[int], first: float = 1.0) -> List[Tuple[int, List[int]]]:
        wait = P3_MIN - (self.clock() - self.last)
        if wait > 0:
            self.sleep(wait)
        f = frame(target, data)
        self.ser.reset_input_buffer()
        self.log(">> " + hex_bytes(f))
        self.ser.write(f)
        if hasattr(self.ser, "flush"):
            self.ser.flush()
        echo = self._read(len(f), 0.5)
        if echo != f:
            self.log("!! эхо не совпало: " + hex_bytes(echo))
        msgs = []
        timeout = first
        while True:
            m = self._read_frame(timeout)
            if m is None:
                break
            msgs.append(m)
            d = m[1]
            # 7F xx 78 — «ответ будет позже»: ждём дольше; иначе — короткая пауза на досылку частей
            timeout = 5.0 if len(d) >= 3 and d[0] == 0x7F and d[2] == 0x78 else 0.06
        self.last = self.clock()
        return msgs

    def kline_connect(self, address: int, init: str = "fast") -> bool:
        """Быстрая инициализация и StartCommunication. 5-бод «Январю» не нужна — не делаем."""
        if init != "fast":
            return False
        self.stop()
        ser = self.ser
        ser.break_condition = False
        self.sleep(0.35)               # линия в покое (W5 ≥ 300 мс)
        ser.reset_input_buffer()
        ser.break_condition = True     # TiniL: 25 мс низкий
        self.sleep(0.025)
        ser.break_condition = False    # 25 мс высокий (TWuP = 50 мс)
        self.sleep(0.025)
        self.last = 0.0
        try:
            msgs = self._exchange(address, [0x81], first=0.4)
        except DiagError as e:
            self.log(f"!! {e}")
            return False
        ok = any(d and d[0] == 0xC1 and src in (address, -1) for src, d in msgs)
        self.log(f"инициализация 0x{address:02X}: {'OK' if ok else 'нет ответа'}")
        if ok:
            self.mode, self.target = "kline", address
        return ok

    def request(self, module, request: bytes) -> List[int]:
        if not is_read_only(request):
            raise DiagError(f"Запрос {hex_bytes(request)} может изменить данные в блоке и заблокирован.")
        address = module.address
        stale = self.clock() - self.last > SESSION_IDLE
        if self.mode != "kline" or self.target != address or stale:
            if not self.kline_connect(address):
                raise DiagError(f"Блок 0x{address:02X} не отвечает на K-line (KKL)")
        msgs = self._exchange(address, list(request))
        if not msgs:
            self.mode = None
            raise DiagError(f"Блок 0x{address:02X} не ответил на {hex_bytes(request)}")
        return final_payload([d for _, d in msgs], request[0])

    def stop(self) -> None:
        if self.mode == "kline" and self.target is not None:
            try:
                self._exchange(self.target, [0x82], first=0.3)
            except DiagError:
                pass
        self.mode = None
        self.target = None

    def close(self, close_port: bool = True) -> None:
        try:
            self.stop()
        finally:
            if close_port:
                self.ser.close()
            self._log.close()
