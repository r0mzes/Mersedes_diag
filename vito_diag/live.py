"""Общий источник живых данных: профили машин, фоновый опрос, переподключение, свежесть значений.

Слои: транспорт (COM-порт / эмулятор) → ElmLink / python-OBD → профиль машины (здесь) →
интерфейс (веб-панель, окно). Профиль умеет три вещи: open() — подключиться и прочитать
блок и ошибки, sample() — один замер {имя: значение}, close().

Poller крутит профиль в своём потоке: при обрыве закрывает связь и переподключается с паузой,
у каждого значения хранит время замера — интерфейс показывает «устарело», а не застывшие цифры.
Только чтение: профили отправляют лишь запросы, которые пропускает защита ElmLink.
"""
from __future__ import annotations

import csv
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from vito_diag import january

# Подпись и единицы для плиток: имя -> (подпись, единицы).
LADA_META: Dict[str, Tuple[str, str]] = dict(january.ALL_META)

# Норма для прогретого мотора на холостом: вне диапазона плитка подсвечивается.
NORMS: Dict[str, Tuple[float, float]] = {
    "ECT": (80, 105), "LTFT": (-10, 10), "STFT": (-10, 10), "VOLT": (13.2, 14.8),
    "COOLANT_TEMP": (80, 105), "CONTROL_MODULE_VOLTAGE": (13.2, 14.8),
}

# Плитки по умолчанию (остальные включаются в настройках панели).
DEFAULT_TILES = {
    "lada": ["ECT", "RPM", "AFR", "INJ_MS", "LTFT", "STFT", "INJ_CORR", "O2S1", "MAF", "TPS", "TIMING", "FUEL_LH"],
    "obd": ["RPM", "COOLANT_TEMP", "ENGINE_LOAD", "MAF", "INTAKE_PRESSURE", "CONTROL_MODULE_VOLTAGE"],
}


class LadaProfile:
    """ВАЗ «Январь» / M7.9.7 / М73 / М74: через ElmLink или кабель KKL (kkl=True), K-line, контакт 7."""

    kind = "lada"
    meta = LADA_META

    def __init__(self, port: str = "", baudrate: int = 38400, hub: str = "", ser_factory=None,
                 log_dir: str = "logs", addresses=january.ADDRESSES, kkl: bool = False):
        self.port, self.baudrate, self.hub_port, self.kkl = port, baudrate, hub, kkl
        self.layout = ""
        self.ser_factory = ser_factory  # эмулятор: фабрика объекта с интерфейсом pyserial
        self.log_dir, self.addresses = log_dir, addresses
        self.link = self.hub = self.module = None
        self.pids: List[int] = []
        self.fuel_system = ""

    def open(self) -> dict:
        from vito_diag.elm import ElmLink

        if self.hub_port:
            from vito_diag.hub import Hub

            self.hub = Hub(self.hub_port)
            self.hub.switch.select("7")
        ser = self.ser_factory() if self.ser_factory else None
        if self.kkl:
            from vito_diag.kkl import KklLink

            self.link = KklLink(self.port or "emu", ser=ser, log_dir=self.log_dir)
        else:
            self.link = ElmLink(self.port or "emu", baudrate=self.baudrate, ser=ser, log_dir=self.log_dir)
        self.module = january.connect(self.link, self.addresses)
        if self.module is None:
            raise ConnectionError("блок не отвечает на K-line (адреса "
                                  + ", ".join(f"0x{a:02X}" for a in self.addresses) + ")")
        rep = january.LadaReport(address=self.module.address)
        january.identify(self.link, self.module, rep)
        january.read_dtcs(self.link, self.module, rep)
        self.pids = january.supported_pids(self.link, self.module)
        self.fuel_system = january.read_fuel_system(self.link, self.module) if 0x03 in self.pids else ""
        notes = list(rep.notes)
        self.layout = "obd" if self.pids else ""
        if not self.pids:
            first = january.parse_hex(january.read_2101(self.link, self.module))
            if january.decode_2101(first):
                self.layout = "j51"
                self.fuel_system = january.j51_fuel_system(first)
            else:
                notes.append("Блок не отдаёт стандартные живые данные (вероятно, Евро-2 без лямбды).")
        extra = [rep.ident.get(k, "") for k in ("sw", "engine", "date", "ecu_id")]
        return {
            "title": f"ВАЗ «Январь», K-line 0x{rep.address:02X}",
            "ecu": rep.ecu_number, "vin": rep.vin,
            "ident": " · ".join(x for x in extra if x) if rep.ident else rep.ident_text,
            "adapter": (self.link.version or "").splitlines()[-1] if self.link.version else "",
            "voltage": self.link.voltage(),
            "dtcs": [{"code": d.code, "description": d.description,
                      "status": ", ".join(d.extra.get("status", []))} for d in rep.dtcs],
            "dtc_error": rep.dtc_error, "notes": notes, "fuel_system": self.fuel_system,
            "log": str(self.link.log_path),
        }

    def sample(self) -> Dict[str, float]:
        if self.layout == "j51":
            raw = january.parse_hex(january.read_2101(self.link, self.module))
            values = january.decode_2101(raw)
            if not values:
                raise ConnectionError("блок перестал отвечать")
            self.fuel_system = january.j51_fuel_system(raw)
            return values
        if not self.pids:
            raise ConnectionError("нет живых данных")
        values = january.read_sample(self.link, self.module, self.pids)
        if not values:
            # Ни один PID не ответил — связь потеряна (блок выключили, адаптер отвалился).
            raise ConnectionError("блок перестал отвечать")
        return values

    def hints(self, samples: List[Dict[str, float]]) -> List[str]:
        return january.check_mixture(samples, self.fuel_system)

    def close(self):
        try:
            if self.link is not None:
                if self.link.mode == "kline":
                    self.link.cmd("ATPC")
                self.link.close()
        except Exception:
            pass
        finally:
            self.link = None
            if self.hub is not None:
                try:
                    self.hub.close()
                except Exception:
                    pass
                self.hub = None


class ObdProfile:
    """Общий OBD-II (python-OBD) — Mercedes и любые машины; demo=True — симулятор Vito."""

    kind = "obd"

    def __init__(self, port: str = "", baudrate: Optional[int] = None, protocol: Optional[str] = None,
                 demo: bool = False):
        from vito_diag.sources import LIVE_PARAMS

        self.port, self.baudrate, self.protocol, self.demo = port, baudrate, protocol, demo
        self.src = None
        self.meta: Dict[str, Tuple[str, str]] = {n: (label, "") for n, label in LIVE_PARAMS}

    def open(self) -> dict:
        from vito_diag.dtc import lookup
        from vito_diag.sources import ObdSource, SimulatedSource

        self.src = SimulatedSource(seed=1) if self.demo else ObdSource(self.port or None, self.baudrate,
                                                                       self.protocol)
        info = self.src.info()
        dtcs = []
        for source, items in (("сохранена", self.src.read_dtcs()), ("ожидает", self.src.read_pending_dtcs())):
            for code, desc in items:
                dtcs.append({"code": code, "description": desc or lookup(code).description, "status": source})
        return {"title": "OBD-II" + (" (демо)" if self.demo else ""), "ecu": "", "vin": info.get("VIN", ""),
                "ident": info.get("Протокол", ""), "adapter": info.get("Порт", ""), "voltage": "",
                "dtcs": dtcs, "dtc_error": "", "notes": [], "fuel_system": "", "log": ""}

    def sample(self) -> Dict[str, float]:
        out = {}
        for name, (label, value, unit) in self.src.read_live().items():
            if value is not None:
                out[name] = value
                self.meta[name] = (label, unit)
        if not out:
            raise ConnectionError("нет ответа на запросы живых данных")
        return out

    def hints(self, samples: List[Dict[str, float]]) -> List[str]:
        from vito_diag.analyzer import check_live

        if not samples:
            return []
        last = samples[-1]
        return check_live({n: (self.meta.get(n, (n, ""))[0], v, "") for n, v in last.items()})

    def close(self):
        if self.src is not None:
            try:
                self.src.close()
            except Exception:
                pass
            self.src = None


class Poller:
    """Фоновый опрос профиля с переподключением. snapshot() — состояние для интерфейса."""

    HISTORY = 300

    def __init__(self, profile, interval: float = 0.2, stale_after: float = 5.0,
                 record_dir: Optional[str] = "reports", clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.profile = profile
        self.interval, self.stale_after = interval, stale_after
        self.record_dir, self.clock, self.sleep = record_dir, clock, sleep
        self.status, self.message = "connecting", "подключение…"
        self.info: dict = {}
        self.values: Dict[str, Tuple[float, float]] = {}  # имя -> (значение, время замера)
        self.history: deque = deque(maxlen=self.HISTORY)  # (время, замер)
        self.hints: List[str] = []
        self.samples = 0
        self.reconnects = 0
        self.csv_path = ""
        self.version = 0
        self.changed = threading.Condition()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._csv = self._writer = None

    # ---- поток ----
    def start(self):
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.profile.close()
        if self._csv:
            self._csv.close()
        self._set("stopped", "остановлено")

    def run(self, max_cycles: Optional[int] = None):
        backoff = 1.0
        connected = False
        cycles = 0
        while not self._stop.is_set() and (max_cycles is None or cycles < max_cycles):
            cycles += 1
            if not connected:
                self._set("connecting", "подключение…")
                try:
                    self.info = self.profile.open()
                    connected, backoff = True, 1.0
                    self._set("online", "на связи")
                except Exception as e:  # порт занят, блок молчит, BT отвалился…
                    self.profile.close()
                    self._set("lost", f"нет связи: {e}. Повтор через {backoff:.0f} с")
                    self.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue
            try:
                sample = self.profile.sample()
            except Exception as e:
                self.profile.close()
                connected = False
                self.reconnects += 1
                self._set("lost", f"связь потеряна: {e}")
                continue
            self._add(sample)
            if self.interval:
                self.sleep(self.interval)

    # ---- данные ----
    def _set(self, status: str, message: str):
        with self.changed:
            self.status, self.message = status, message
            self.version += 1
            self.changed.notify_all()

    def _add(self, sample: Dict[str, float]):
        now = self.clock()
        with self.changed:
            for k, v in sample.items():
                self.values[k] = (v, now)
            self.history.append((now, sample))
            self.samples += 1
            self.hints = self.profile.hints([s for _, s in list(self.history)[-60:]])
            self.version += 1
            self.changed.notify_all()
        self._record(now, sample)

    def _record(self, now: float, sample: Dict[str, float]):
        if not self.record_dir:
            return
        if self._writer is None:
            out = Path(self.record_dir)
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{self.profile.kind}_live_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"
            self._csv = open(path, "w", newline="", encoding="utf-8")
            self._fields = ["time"] + list(self.profile.meta)
            self._writer = csv.DictWriter(self._csv, fieldnames=self._fields, delimiter=";",
                                          extrasaction="ignore")
            self._writer.writeheader()
            self.csv_path = str(path)
        self._writer.writerow({"time": datetime.fromtimestamp(now).strftime("%H:%M:%S.%f")[:-3], **sample})
        self._csv.flush()

    def wait(self, version: int, timeout: float = 1.0) -> int:
        """Ждать изменения состояния после version; вернуть текущую версию."""
        with self.changed:
            if self.version == version:
                self.changed.wait(timeout)
            return self.version

    def snapshot(self, history: bool = False) -> dict:
        now = self.clock()
        with self.changed:
            values = {}
            running = self.values.get("RPM", (0, 0))[0] > 400
            for name, (v, t) in self.values.items():
                label, unit = self.profile.meta.get(name, (name, ""))
                lo_hi = NORMS.get(name)
                values[name] = {
                    "label": label, "unit": unit, "value": round(v, 3), "age": round(now - t, 1),
                    "stale": now - t > self.stale_after,
                    "bad": bool(lo_hi and running and not lo_hi[0] <= v <= lo_hi[1]),
                }
            snap = {
                "profile": self.profile.kind, "status": self.status, "message": self.message,
                "info": self.info, "values": values, "hints": self.hints, "samples": self.samples,
                "reconnects": self.reconnects, "csv": self.csv_path, "version": self.version,
                "default_tiles": DEFAULT_TILES.get(self.profile.kind, []),
            }
            if history:
                snap["history"] = [{"t": round(t - now, 1), **s} for t, s in self.history]
            return snap
