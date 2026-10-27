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


class KklJanuary51:
    """Кабель KKL + «Январь-5.1» (K-line напрямую, без ELM327): эхо передачи, быстрая инициализация
    через break, кадры KWP2000. Для `web --kkl --demo` и тестов vito_diag.kkl.

    Отвечает как блок 2026-10-27 (C1 6B 8F, 1A 80 по полям, 01 xx — NRC 11, 21 01 — 34 байта),
    значения — по сценарию: "rich" — прошивка задаёт богатую смесь, лямбда в «богато», коррекция
    урезает впрыск; "ok" — исправный мотор.
    """

    def __init__(self, scenario: str = "rich", clock: Callable[[], float] = time.monotonic):
        if scenario not in SCENARIOS:
            raise ValueError(f"сценарий {scenario!r}: есть {', '.join(SCENARIOS)}")
        self.scenario, self.clock, self.start = scenario, clock, clock()
        self.out = b""
        self.inbuf = b""
        self.awake = False      # быстрая инициализация была
        self.session = False    # StartCommunication принят
        self._break_at = None
        self.n = 0
        self.sent: List[str] = []

    # --- pyserial ---
    @property
    def in_waiting(self):
        return len(self.out)

    @property
    def break_condition(self):
        return self._break_at is not None

    @break_condition.setter
    def break_condition(self, on):
        if on:
            self._break_at = self.clock()
            self.session = False
        elif self._break_at is not None:
            self.awake = True   # длительность не проверяем: часы в тестах могут стоять
            self._break_at = None

    def read(self, n=1):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def reset_input_buffer(self):
        self.out = b""

    def flush(self):
        pass

    def close(self):
        pass

    def write(self, data):
        self.out += bytes(data)  # эхо: кабель слышит свою передачу
        self.inbuf += bytes(data)
        while self.inbuf:
            fmt = self.inbuf[0]
            hdr = 3 if fmt & 0x3F else 4
            if len(self.inbuf) < hdr:
                return
            length = fmt & 0x3F or self.inbuf[3]
            total = hdr + length + 1
            if len(self.inbuf) < total:
                return
            f, self.inbuf = self.inbuf[:total], self.inbuf[total:]
            if f[1] != 0x10 or sum(f[:-1]) & 0xFF != f[-1]:
                continue
            req = list(f[hdr:hdr + length])
            self.sent.append(" ".join(f"{b:02X}" for b in req))
            answer = self._answer(req)
            if answer is not None:
                self.out += self._frame(answer)

    @staticmethod
    def _frame(data: List[int]) -> bytes:
        head = [0x80 | len(data), 0xF1, 0x10] if len(data) < 64 else [0x80, 0xF1, 0x10, len(data)]
        body = head + list(data)
        return bytes(body + [sum(body) & 0xFF])

    # --- блок ---
    def _answer(self, req: List[int]):
        if req == [0x81]:
            if not self.awake:
                return None
            self.awake, self.session = False, True
            return [0xC1, 0x6B, 0x8F]
        if not self.session:
            return None
        if req == [0x82]:
            self.session = False
            return [0xC2]
        if req[:1] == [0x3E]:
            return [0x7E]
        if req == [0x1A, 0x80]:
            fields = [("", 19), ("2112-1411020-41", 16), ("DEMO", 10), ("J5V05L19", 10),
                      ("2112 1.5 16v", 15), ("", 7), ("01-01-2003", 10), ("EMULATOR", 8)]
            data = [0x5A, 0x80]
            for text, n in fields:
                raw = list(text.encode()) if text else [0xFF] * n
                data += (raw + [0x20] * n)[:n]
            return data
        if req[:2] == [0x18, 0x00]:
            return [0x58, 0x01, 0x01, 0x72, 0xE0] if self.scenario == "rich" else [0x58, 0x00]
        if req == [0x21, 0x01]:
            return [0x61, 0x01] + self._block()
        return [0x7F, req[0], 0x11]

    def _block(self) -> List[int]:
        t = self.clock() - self.start
        self.n += 1
        rich = self.scenario == "rich"
        noise = math.sin(self.n * 1.7)
        ect = min(90.0, 62 + t * 0.5)
        warm = ect >= 70
        afr = 12.4 if rich else 14.7
        corr = 0.86 if rich and warm else 1.0 + (0.03 * noise if warm else 0)
        o2 = (0.78 + 0.08 * math.sin(t * 2.3 + self.n)) if rich else (0.45 + 0.38 * math.sin(t * 2.3 + self.n))
        rpm = 820 + 25 * math.sin(t / 3)
        maf = (15.5 if rich else 9.0) + 0.6 * math.sin(t / 2.5)
        inj = (3.6 if rich else 2.6) + 0.1 * noise
        d = [0] * 34
        d[0] = 0x1F                                   # комплектация: лямбда, адсорбер, …
        d[2] = 0x02 | (0x10 if warm else 0)           # холостой ход, работа по лямбде на прогретом
        d[8] = round(ect + 40)
        d[9] = round(afr / 14.7 * 256 - 128)
        d[11] = round(rpm / 40)
        d[12] = round(rpm / 10) & 0xFF
        d[13] = d[14] = 40
        d[15] = round(corr * 256 - 128)
        d[16] = round(12 + 1.5 * math.sin(t / 2)) * 2
        d[18] = round((14.1 + 0.05 * noise - 5.2) / 0.05)
        d[19] = 85
        d[20] = round(o2 * 256 / 1.25)
        d[21] = 0x03
        for i, v in ((22, inj * 125), (24, maf * 10), (26, maf / 3.6 * 6 * 2), (28, maf / 14.7 * 50 / 0.75 / 3.6)):
            v = round(v)
            d[i], d[i + 1] = v & 0xFF, v >> 8
        d[30] = d[31] = 0xFF                          # путевой расход — стоим
        d[32], d[33] = 0x40, 0xB1
        return d
