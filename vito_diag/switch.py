"""Автоматический переключатель линий OBD (Arduino, firmware/obd_switch).

Реле подают на контакт 7 адаптера один из контактов машины 7/8/9/11, при
необходимости переключают CAN адаптера на другую пару и обесточивают адаптер.
Заодно Arduino меряет напряжения на контактах разъёма (см. docs/AUTO_SWITCH.md).
"""

import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

LINES = ("7", "8", "9", "11")

# Наша сборка (docs/AUTO_SWITCH.md): HC-06 переключателя и ELM327 «OBD II».
DEFAULT_HC06 = "98:d3:41:00:0f:55"
DEFAULT_ELM = "01:2d:a1:86:68:c0"


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

    def session(self):
        """Группа команд (нужно мосту ESP32, по USB ничего не делает)."""
        return nullcontext(self)

    def cmd(self, command: str, wait: float = 2.0) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((command + "\n").encode("ascii"))
        buf = b""
        text = ""
        deadline = time.time() + wait
        while time.time() < deadline and not text:
            buf += self.ser.read(self.ser.in_waiting or 1)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", errors="ignore").strip()
                # строки моста ESP32 ("~DISCONNECTED" и т. п.) — не ответ переключателя
                if line and not line.startswith("~"):
                    text = line
                    break
        if not text:
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


class Bridge:
    """Служебные команды моста ESP32 (firmware/esp32_obd_bridge): к кому он подключён."""

    def __init__(self, ser, log=print):
        self.ser = ser
        self.log = log
        self.scan_first = False

    def _send(self, command: str) -> None:
        self.ser.reset_input_buffer()
        self.ser.write(("~" + command + "\n").encode("ascii"))

    def _wait_line(self, prefixes: Tuple[str, ...], timeout: float) -> str:
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            buf += self.ser.read(self.ser.in_waiting or 1)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", errors="ignore").strip()
                if line.startswith(prefixes):
                    return line
        return ""

    def state(self) -> Tuple[bool, str]:
        """(подключён ли, сохранённый адрес)."""
        self._send("STATE")
        line = self._wait_line(("~STATE",), 3.0)
        if not line:
            raise SwitchError("Мост ESP32 не ответил на ~STATE — это точно порт моста?")
        fields = dict(p.split("=", 1) for p in line.split()[1:] if "=" in p)
        return fields.get("connected") == "1", fields.get("addr", "").lower()

    def _scan(self) -> None:
        self._send("SCAN")
        self._wait_line(("~SCAN done", "~SCAN failed"), 20.0)

    def use(self, addr: str, attempts: int = 3) -> None:
        """Переключить мост на устройство addr. Если не вышло — ~SCAN и ещё раз:
        после смены устройства первое подключение у моста обычно не проходит.
        Раз так случилось, дальше ищем сразу, не дожидаясь отказа (экономит ~8 с)."""
        if self.scan_first:
            self._scan()
        for i in range(attempts):
            self._send("USE " + addr)
            line = self._wait_line(("~CONNECTED", "~connect failed"), 25.0)
            if line == "~CONNECTED":
                time.sleep(0.5)
                self.ser.reset_input_buffer()
                return
            self.scan_first = True
            if i < attempts - 1:
                self.log(f"  мост: нет связи с {addr}, ищу устройства и пробую ещё раз...")
                self._scan()
        raise SwitchError(f"Мост ESP32 не подключился к {addr} за {attempts} попытки")


class BridgeSwitch(LineSwitch):
    """Переключатель, к которому ходим через тот же мост ESP32, что и к ELM327.

    Мост держит одно соединение, поэтому на время команд переключателя он
    переподключается к HC-06, а потом обратно к адаптеру (~30 с на каждую смену).
    Без связи с компьютером Arduino не сбрасывается: выбранная линия остаётся.
    """

    def __init__(self, ser, hc06: str = DEFAULT_HC06, elm: Optional[str] = None, log=print,
                 identify: bool = True):
        self.bridge = Bridge(ser, log)
        self.hc06 = hc06.lower()
        connected, addr = self.bridge.state()
        if addr and addr != self.hc06:
            elm = elm or addr
        self.elm = (elm or DEFAULT_ELM).lower()
        self.log = log
        self.require_elm = True  # False — не ошибка, если адаптер не ответил (проверка на столе)
        self._on_switch = False
        self.ser = ser
        self.line = None
        self.version = None
        self.last_state = ""
        if identify:
            with self.session():
                pass  # ID и STATE спрашиваются при первом подключении

    def _identify(self) -> None:
        ident = LineSwitch.cmd(self, "ID", wait=5.0)
        if not ident.startswith("OBDSW"):
            raise SwitchError(f"HC-06 {self.hc06} ответил не как переключатель OBD: {ident!r}")
        self.version = ident
        self.last_state = LineSwitch.cmd(self, "STATE")
        if self.last_state.startswith("STATE SEL"):
            self.line = self.last_state.split()[2]

    class _Session:
        def __init__(self, sw):
            self.sw = sw

        def __enter__(self):
            if not self.sw._on_switch:
                self.sw.log("  мост: подключаюсь к переключателю...")
                self.sw.bridge.use(self.sw.hc06)
                time.sleep(1.0)  # первые байты после подключения HC-06 бывают мусорные
                self.sw._on_switch = True
                if self.sw.version is None:
                    self.sw._identify()
            return self.sw

        def __exit__(self, exc_type, exc, tb):
            self.sw._on_switch = False
            self.sw.log("  мост: обратно к адаптеру...")
            try:
                self.sw.bridge.use(self.sw.elm)
            except SwitchError as e:
                if self.sw.require_elm and exc_type is None:
                    raise
                self.sw.log(f"  ! {e}. Мост будет сам пытаться подключиться к адаптеру.")
            return False

    def session(self):
        """Несколько команд переключателю за одно подключение."""
        if self._on_switch:
            return LineSwitch.session(self)
        return BridgeSwitch._Session(self)

    def cmd(self, command: str, wait: float = 4.0) -> str:
        with self.session():
            return LineSwitch.cmd(self, command, wait=wait)

    def select(self, line) -> None:
        if str(line) == self.line:
            return
        with self.session():
            LineSwitch.select(self, line)
            self.last_state = LineSwitch.state(self)
        self.line = str(line)

    def state(self) -> str:
        """Вне сеанса — последнее известное состояние (лишний раз к HC-06 не ходим)."""
        if self._on_switch:
            self.last_state = LineSwitch.state(self)
        return self.last_state

    def reset_adapter(self, off_seconds: float = 2.0) -> None:
        with self.session():
            LineSwitch.reset_adapter(self, off_seconds)

    def close(self, restore: bool = True) -> None:
        # порт общий с адаптером: закрывает его ElmLink (или cmd_switch)
        if restore and self.line not in (None, "7"):
            self.cmd("RESET")
            self.line = "7"


def is_bridge_spec(spec: Optional[str]) -> bool:
    return bool(spec) and spec.lower().split(":", 1)[0] == "bt"


def open_bridge_switch(spec: str, ser, log=print, identify: bool = True) -> BridgeSwitch:
    """spec: 'bt' (HC-06 нашей сборки) или 'bt:aa:bb:cc:dd:ee:ff'."""
    parts = spec.split(":", 1)
    return BridgeSwitch(ser, hc06=parts[1] if len(parts) > 1 else DEFAULT_HC06, log=log,
                        identify=identify)


def open_switch(port: Optional[str]) -> Optional[LineSwitch]:
    return LineSwitch(port) if port else None
