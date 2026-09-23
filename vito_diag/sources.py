"""Источники данных: реальный автомобиль (python-OBD + ELM327) и симулятор."""

import logging
import random
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Параметры, которые читаем в режиме live (имена команд python-OBD).
LIVE_PARAMS: List[Tuple[str, str]] = [
    ("RPM", "Обороты двигателя"),
    ("SPEED", "Скорость"),
    ("COOLANT_TEMP", "Температура ОЖ"),
    ("INTAKE_TEMP", "Температура воздуха на впуске"),
    ("ENGINE_LOAD", "Нагрузка двигателя"),
    ("INTAKE_PRESSURE", "Давление во впуске (наддув, абс.)"),
    ("BAROMETRIC_PRESSURE", "Атмосферное давление"),
    ("MAF", "Расход воздуха (MAF)"),
    ("FUEL_RAIL_PRESSURE_DIRECT", "Давление в топливной рампе"),
    ("COMMANDED_EGR", "Заданное открытие EGR"),
    ("CONTROL_MODULE_VOLTAGE", "Напряжение бортсети (ЭБУ)"),
    ("RUN_TIME", "Время работы с запуска"),
    ("DISTANCE_W_MIL", "Пробег с горящим Check Engine"),
    ("DISTANCE_SINCE_DTC_CLEAR", "Пробег после сброса ошибок"),
]


class Source:
    """Общий интерфейс источника данных."""

    name = "base"

    def info(self) -> Dict[str, str]:
        raise NotImplementedError

    def read_dtcs(self) -> List[Tuple[str, str]]:
        """Сохранённые (подтверждённые) ошибки, режим OBD 03."""
        raise NotImplementedError

    def read_pending_dtcs(self) -> List[Tuple[str, str]]:
        """Ожидающие (неподтверждённые) ошибки, режим OBD 07."""
        raise NotImplementedError

    def read_live(self) -> Dict[str, Tuple[str, Optional[float], str]]:
        """{команда: (подпись, значение, единицы)}"""
        raise NotImplementedError

    def clear_dtcs(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ObdSource(Source):
    """Реальный автомобиль через адаптер ELM327 (USB / Bluetooth / Wi-Fi)."""

    name = "obd"

    def __init__(self, port: Optional[str] = None, baudrate: Optional[int] = None,
                 protocol: Optional[str] = None, timeout: float = 5.0):
        import obd  # импорт здесь, чтобы демо-режим работал без библиотеки

        self._obd = obd
        # port=None -> python-OBD сам переберёт доступные последовательные порты.
        # Для Wi-Fi адаптера: port="socket://192.168.0.10:35000"
        self.conn = obd.OBD(portstr=port, baudrate=baudrate, protocol=protocol,
                            fast=False, timeout=timeout)
        if not self.conn.is_connected():
            status = self.conn.status()
            self.conn.close()
            raise ConnectionError(
                f"Не удалось связаться с автомобилем (статус: {status}). "
                "Проверьте: адаптер вставлен в OBD-разъём, зажигание ВКЛЮЧЕНО, "
                "выбран правильный порт (python -m vito_diag ports)."
            )

    def _query(self, name: str):
        cmds = self._obd.commands
        if not cmds.has_name(name):
            return None
        cmd = cmds[name]
        if not self.conn.supports(cmd):
            return None
        resp = self.conn.query(cmd)
        return None if resp.is_null() else resp.value

    def info(self) -> Dict[str, str]:
        result = {
            "Порт": str(self.conn.port_name()),
            "Протокол": f"{self.conn.protocol_name()} ({self.conn.protocol_id()})",
            "Статус": str(self.conn.status()),
        }
        vin = self._query("VIN")
        if vin:
            result["VIN"] = vin.decode(errors="ignore") if isinstance(vin, (bytes, bytearray)) else str(vin)
        status = self._query("STATUS")
        if status is not None:
            result["Check Engine (MIL)"] = "ГОРИТ" if status.MIL else "не горит"
            result["Кол-во ошибок (по ЭБУ)"] = str(status.DTC_count)
        return result

    def _dtcs(self, name: str) -> List[Tuple[str, str]]:
        cmd = self._obd.commands[name]
        resp = self.conn.query(cmd, force=True)
        return list(resp.value or []) if not resp.is_null() else []

    def read_dtcs(self):
        return self._dtcs("GET_DTC")

    def read_pending_dtcs(self):
        return self._dtcs("GET_CURRENT_DTC")

    def read_live(self):
        out = {}
        for name, label in LIVE_PARAMS:
            value = self._query(name)
            if value is None:
                continue
            if hasattr(value, "magnitude"):
                out[name] = (label, float(value.magnitude), f"{value.units:~}")
            else:
                out[name] = (label, None, str(value))
        return out

    def clear_dtcs(self) -> bool:
        resp = self.conn.query(self._obd.commands.CLEAR_DTC, force=True)
        return not resp.is_null()

    def close(self):
        self.conn.close()


class SimulatedSource(Source):
    """Демо-режим без автомобиля: типичный набор ошибок дизельного Vito."""

    name = "demo"

    def __init__(self, seed: Optional[int] = None):
        self._rnd = random.Random(seed)
        self._stored = [("P0299", ""), ("P2463", ""), ("P0401", ""), ("P0671", "")]
        self._pending = [("P2453", "")]

    def info(self):
        return {
            "Порт": "симулятор",
            "Протокол": "ISO 15765-4 (CAN 11/500) — эмуляция",
            "VIN": "WDF63970313000000 (пример)",
            "Check Engine (MIL)": "ГОРИТ" if self._stored else "не горит",
            "Кол-во ошибок (по ЭБУ)": str(len(self._stored)),
        }

    def read_dtcs(self):
        return list(self._stored)

    def read_pending_dtcs(self):
        return list(self._pending)

    def read_live(self):
        r = self._rnd
        rpm = r.uniform(750, 820)
        values = {
            "RPM": ("Обороты двигателя", rpm, "rpm"),
            "SPEED": ("Скорость", 0.0, "kph"),
            "COOLANT_TEMP": ("Температура ОЖ", r.uniform(84, 90), "degC"),
            "INTAKE_TEMP": ("Температура воздуха на впуске", r.uniform(20, 30), "degC"),
            "ENGINE_LOAD": ("Нагрузка двигателя", r.uniform(18, 25), "percent"),
            "INTAKE_PRESSURE": ("Давление во впуске (наддув, абс.)", r.uniform(98, 104), "kPa"),
            "BAROMETRIC_PRESSURE": ("Атмосферное давление", 100.0, "kPa"),
            "MAF": ("Расход воздуха (MAF)", r.uniform(9, 13), "gps"),
            "FUEL_RAIL_PRESSURE_DIRECT": ("Давление в топливной рампе", r.uniform(24000, 27000), "kPa"),
            "CONTROL_MODULE_VOLTAGE": ("Напряжение бортсети (ЭБУ)", r.uniform(13.9, 14.3), "V"),
        }
        return {k: (lbl, round(v, 1), u) for k, (lbl, v, u) in values.items()}

    def clear_dtcs(self):
        self._stored.clear()
        self._pending.clear()
        return True
