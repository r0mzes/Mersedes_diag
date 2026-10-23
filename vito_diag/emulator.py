"""Эмулятор ELM327 с блоком «Январь» за ним — для работы над интерфейсом без машины.

Подставляется в ElmLink вместо COM-порта (ElmLink(..., ser=JanuaryEmulator())), поэтому
демо идёт через тот же код протокола, что и на машине: инициализация K-line, 1A 80, 18 00,
служба 01, 21 01. Значения меняются во времени по сценарию.

Сценарий "rich" — типичный перелив: мотор прогревается до ~90 °C, долговременная коррекция
−18 %, лямбда почти всё время «богато», ДМРВ на холостом завышает (~15,5 кг/ч), ошибка P0172.
Сценарий "ok" — исправный мотор для сравнения.
"""
from __future__ import annotations

import math
import time
from typing import Callable, Dict, List

SCENARIOS = ("rich", "ok")


def _frame(data: List[int]) -> str:
    """Сообщение KWP2000 от блока 0x10 тестеру: 80 F1 10 <длина> <данные> <КС>."""
    body = [0x80, 0xF1, 0x10, len(data)] + list(data)
    return " ".join(f"{b:02X}" for b in body + [sum(body) & 0xFF])


class JanuaryEmulator:
    """Интерфейс pyserial (read / write / in_waiting) поверх эмуляции ELM327 v1.5 + «Январь»."""

    def __init__(self, scenario: str = "rich", clock: Callable[[], float] = time.monotonic):
        if scenario not in SCENARIOS:
            raise ValueError(f"сценарий {scenario!r}: есть {', '.join(SCENARIOS)}")
        self.scenario = scenario
        self.clock = clock
        self.start = clock()
        self.out = b""
        self.header = ""
        self.kline_ok = False
        self.n = 0

    # --- pyserial ---
    @property
    def in_waiting(self):
        return len(self.out)

    def read(self, n=1):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def reset_input_buffer(self):
        self.out = b""

    def close(self):
        pass

    def write(self, data):
        cmd = data.decode().strip().upper().replace(" ", "")
        self.out += (self._answer(cmd) + "\r\r>").encode()

    # --- ELM327 ---
    def _answer(self, c: str) -> str:
        if c == "ATZ":
            self.kline_ok = False
            return "ELM327 v1.5 (emulator)"
        if c == "ATRV":
            return f"{self.values()['VOLT']:.1f}V"
        if c.startswith("ATSH"):
            self.header = c[4:]
            return "OK"
        if c == "ATFI":
            self.kline_ok = self.header == "8110F1"
            return "BUS INIT: OK" if self.kline_ok else "BUS INIT: ...ERROR"
        if c == "ATPC":
            self.kline_ok = False
            return "OK"
        if c.startswith("AT"):
            return "OK"
        if not self.kline_ok:
            return "BUS INIT: ...UNABLE TO CONNECT"
        return self._ecu(c)

    # --- блок ---
    def values(self) -> Dict[str, float]:
        t = self.clock() - self.start
        self.n += 1
        rich = self.scenario == "rich"
        noise = math.sin(self.n * 1.7)
        o2_mid, o2_swing = (0.78, 0.08) if rich else (0.45, 0.38)  # богато: зонд не уходит вниз
        return {
            "ECT": min(90.0, 62 + t * 0.5),
            "RPM": 820 + 25 * math.sin(t / 3) + 8 * noise,
            "LTFT": -18.0 if rich else 1.6,
            "STFT": (-3 if rich else 0) + 4 * math.sin(t * 2.1),
            "O2S1": o2_mid + o2_swing * math.sin(t * 2.3 + self.n),
            "O2S2": 0.7 if rich else 0.62,
            "MAF": (15.5 if rich else 9.0) + 0.6 * math.sin(t / 2.5),
            "TPS": 0.0,
            "TIMING": 12 + 1.5 * math.sin(t / 2),
            "IAT": 24.0,
            "LOAD": 22 + 2 * noise,
            "MAP": 34 + noise,
            "SPEED": 0.0,
            "VOLT": 14.1 + 0.05 * noise,
            "RUNTIME": t,
        }

    def _ecu(self, c: str) -> str:
        if c == "3E01":
            return _frame([0x7E, 0x01])
        if c == "1A80":
            text = b"XTA21120000000001  2112-1411020-75  DEMO " + self.scenario.upper().encode()
            return _frame([0x5A, 0x80] + list(text))
        if c == "1800FF00":
            if self.scenario == "rich":
                return _frame([0x58, 0x01, 0x01, 0x72, 0xE0])  # P0172 — смесь слишком богатая
            return _frame([0x58, 0x00])
        if c == "2101":
            v = self.values()
            return _frame([0x61, 0x01, int(v["ECT"]) + 40, int(v["RPM"]) // 40, 0x00, 0x5A])
        if c.startswith("01") and len(c) == 4:
            return self._pid(int(c[2:], 16))
        return _frame([0x7F, int(c[:2], 16), 0x11])

    PIDS = (0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11,
            0x14, 0x15, 0x1F, 0x20, 0x40, 0x42)

    def _pid(self, pid: int) -> str:
        if pid in (0x00, 0x20, 0x40):
            mask = 0
            for p in self.PIDS:
                if pid < p <= pid + 0x20:
                    mask |= 1 << (32 - (p - pid))
            return _frame([0x41, pid] + list(mask.to_bytes(4, "big")))
        v = self.values()
        trim = (lambda x: [max(0, min(255, round(x * 128 / 100 + 128)))])
        data = {
            0x03: [0x02, 0x00],
            0x04: [round(v["LOAD"] * 255 / 100)],
            0x05: [round(v["ECT"] + 40)],
            0x06: trim(v["STFT"]),
            0x07: trim(v["LTFT"]),
            0x0B: [round(v["MAP"])],
            0x0C: list(round(v["RPM"] * 4).to_bytes(2, "big")),
            0x0D: [0],
            0x0E: [round((v["TIMING"] + 64) * 2)],
            0x0F: [round(v["IAT"] + 40)],
            0x10: list(round(v["MAF"] / 3.6 * 100).to_bytes(2, "big")),
            0x11: [round(v["TPS"] * 255 / 100)],
            0x14: [round(v["O2S1"] * 200), 0xFF],
            0x15: [round(v["O2S2"] * 200), 0xFF],
            0x1F: list(round(v["RUNTIME"]).to_bytes(2, "big")),
            0x42: list(round(v["VOLT"] * 1000).to_bytes(2, "big")),
        }.get(pid)
        if data is None:
            return _frame([0x7F, 0x01, 0x12])
        return _frame([0x41, pid] + data)
