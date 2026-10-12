"""Общий блок диагностики (firmware/obd_hub, docs/OBD_HUB.md).

Arduino на одном COM-порту: CAN через MCP2515 (команды ELM327, строки с '\\r') и
переключатель линий с замерами (протокол LineSwitch, строки с '\\n'). Порт открывается
один раз, и его делят ElmLink (CAN) и LineSwitch (реле).
"""

import time

from vito_diag.elm import ElmLink
from vito_diag.switch import LineSwitch

HUB_BAUD = 115200


class Hub:
    def __init__(self, port: str, unsafe: bool = False, ser=None, log_dir: str = "logs",
                 boot_wait: float = 2.0):
        if ser is None:
            import serial

            # Uno перезагружается при открытии порта (DTR): реле отпускаются, это безопасно.
            ser = serial.serial_for_url(port, baudrate=HUB_BAUD, timeout=0.1)
            time.sleep(boot_wait)
        self.ser = ser
        try:
            self.switch = LineSwitch(port, ser=ser)
            self.can = ElmLink(port, ser=ser, unsafe=unsafe, log_dir=log_dir)
        except Exception:
            ser.close()
            raise

    def close(self, restore: bool = True) -> None:
        try:
            self.can.close(close_port=False)
        finally:
            self.switch.close(restore=restore)  # RESET реле и закрытие порта
