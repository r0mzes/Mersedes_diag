"""Автоматический переключатель линий OBD (Arduino, firmware/obd_switch).

Реле подают на контакт 7 адаптера один из контактов машины 7/8/9/11, при
необходимости переключают CAN адаптера на другую пару и обесточивают адаптер.
Заодно Arduino меряет напряжения на контактах разъёма (см. docs/AUTO_SWITCH.md).
"""

import time
from dataclasses import dataclass
from typing import Dict, Optional

LINES = ("7", "8", "9", "11")


class SwitchError(Exception):
    pass


@dataclass
class PinReading:
    pin: int
    avg: float
    lo: float
    hi: float

    def verdict(self) -> str:
        """Грубая догадка по напряжению. Это подсказка, а не диагноз."""
        swing = self.hi - self.lo
        if self.avg > 10.5:
            return "~12 В: питание или K-line в покое" + (", есть обмен" if swing > 3 else "")
        if 1.8 <= self.avg <= 3.2 and swing < 2.5:
            return "~2,5 В: похоже на CAN" + (", есть обмен" if swing > 0.3 else ", тихо")
        if self.avg < 0.5 and swing < 0.5:
            return "0 В: масса, обрыв или линия неактивна"
        return "непонятно" + (", сигнал меняется" if swing > 1 else "")


def parse_meas(line: str) -> Dict[int, PinReading]:
    """'MEAS 16:12.41:12.38:12.45 7:...' -> {16: PinReading(...), ...}"""
    parts = line.split()
    if not parts or parts[0] != "MEAS":
        raise SwitchError(f"Неожиданный ответ на MEAS: {line!r}")
    result = {}
    for item in parts[1:]:
        pin, avg, lo, hi = item.split(":")
        result[int(pin)] = PinReading(int(pin), float(avg), float(lo), float(hi))
    return result


class LineSwitch:
    def __init__(self, port: str, baudrate: int = 115200, ser=None, boot_wait: float = 2.0):
        if ser is None:
            import serial

            # Arduino перезагружается при открытии порта (DTR) и возвращает реле в исходное
            # состояние. Это безопасно; программа всё равно выставляет линию заново.
            ser = serial.serial_for_url(port, baudrate=baudrate, timeout=0.1)
            time.sleep(boot_wait)
        self.ser = ser
        self.ser.reset_input_buffer()
        ident = self.cmd("ID", wait=5.0)  # Bluetooth-порт отвечает не сразу
        if not ident.startswith("OBDSW"):
            raise SwitchError(f"На порту {port} не переключатель OBD (ответ: {ident!r})")
        self.version = ident

    def cmd(self, command: str, wait: float = 2.0) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((command + "\n").encode("ascii"))
        buf = b""
        deadline = time.time() + wait
        while time.time() < deadline:
            buf += self.ser.read(self.ser.in_waiting or 1)
            if b"\n" in buf:
                break
        text = buf.decode("ascii", errors="ignore").strip()
        if not text:
            raise SwitchError(f"Переключатель не ответил на {command}")
        if text.startswith("ERR"):
            raise SwitchError(f"Переключатель: {text}")
        return text

    def select(self, line) -> None:
        line = str(line)
        if line not in LINES:
            raise SwitchError(f"Линия {line}: переключатель умеет только {', '.join(LINES)}")
        self.cmd(f"SEL {line}")

    def can_pair(self, alt: bool) -> None:
        self.cmd("CAN ALT" if alt else "CAN STD")

    def adapter_power(self, on: bool) -> None:
        self.cmd("PWR ON" if on else "PWR OFF")

    def reset_adapter(self, off_seconds: float = 2.0) -> None:
        """Передёрнуть питание ELM327 (если он завис)."""
        self.adapter_power(False)
        time.sleep(off_seconds)
        self.adapter_power(True)

    def state(self) -> str:
        return self.cmd("STATE")

    def measure(self) -> Dict[int, PinReading]:
        return parse_meas(self.cmd("MEAS", wait=3.0))

    def close(self, restore: bool = True) -> None:
        try:
            if restore:
                self.cmd("RESET")
        finally:
            self.ser.close()


def open_switch(port: Optional[str]) -> Optional[LineSwitch]:
    return LineSwitch(port) if port else None
