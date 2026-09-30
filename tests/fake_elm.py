"""Эмулятор ELM327 для тестов: один блок на CAN (7E0/7E8, KWP) и один на K-line (0x10)."""


class FakeElm:
    def __init__(self):
        self.out = b""
        self.headers = False
        self.header = ""
        self.protocol = "0"
        self.kline_ok = False
        self.sent = []

    # --- интерфейс pyserial ---
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
        self.sent.append(cmd)
        self.out += (self._answer(cmd) + "\r\r>").encode()

    # --- логика ---
    def _answer(self, c):
        if c == "ATZ":
            return "ELM327 v1.5"
        if c == "ATRV":
            return "12.6V"
        if c.startswith("ATH"):
            self.headers = c == "ATH1"
            return "OK"
        if c.startswith("ATSP"):
            self.protocol = c[4:]
            return "OK"
        if c.startswith("ATSH"):
            self.header = c[4:]
            return "OK"
        if c == "ATFI":
            self.kline_ok = self.header == "8110F1"
            return "BUS INIT: OK" if self.kline_ok else "BUS INIT: ...ERROR"
        if c == "ATSI":
            return "BUS INIT: ...ERROR"
        if c.startswith("AT"):
            return "OK"
        if self.protocol == "6":
            return self._can(c)
        return self._kline(c)

    def _can(self, c):
        if self.header != "7E0":
            return "NO DATA"
        if c.startswith("3E"):
            return "7E8 02 7E 00" if self.headers else "7E 00"
        if c == "1A86":
            # многокадровый ответ: 5A 86 + "A6461234567" (ASCII)
            return "00D\r0: 5A 86 41 36 34 36\r1: 31 32 33 34 35 36 37"
        if c == "18" + "02FF00":
            return "58 02 04 01 E0 20 34 60"
        return "7F " + c[:2] + " 11"

    def _kline(self, c):
        if not self.kline_ok:
            return "NO DATA"
        if c == "1A86":
            return "86 F1 10 5A 86 12 34 56 78 90 00"  # BCD-номер, 6 байт данных + КС
        if c == "1802FF00":
            return "85 F1 10 58 01 07 15 E0 00"
        return "83 F1 10 7F " + c[:2] + " 11 00"


class CloneElm(FakeElm):
    """Клон ELM327 v1.5, как у нас в машине (логи 2026-09-30):
    игнорирует ATSH на CAN (отвечает один блок 0x7E9), многокадровые ответы обрезает до
    первого кадра, ATFI не знает — K-line только автоинициализацией на первом запросе,
    и на K-line тоже игнорирует адрес."""

    def _answer(self, c):
        if c == "ATFI":
            return "?"
        if c == "ATPC":
            self.kline_ok = False
            return "OK"
        if c.startswith("AT") or self.protocol == "6":
            return super()._answer(c)
        if not self.kline_ok:
            # Инициализация уходит не по заданному адресу — всегда отвечает блок 0x10.
            self.kline_ok = True
            return "BUS INIT: OK\r" + self._kline(c)
        return self._kline(c)

    def _can(self, c):
        if c == "3E00":
            return "NO DATA"
        if c == "3E01":
            return "7E9 01 7E" if self.headers else "7E"
        if c == "1802FF00":
            return "008\r0: 58 02 D4 0B 60 17"
        if c == "18020000":
            return "58 01 17 31 70"
        if c == "1802C000":
            return "58 01 D4 0B 60"
        if c == "1A86":
            return "012\r0: 5A 86 00 34 46 41"
        return "7F " + c[:2] + " 31"

    def _kline(self, c):
        if c == "3E01":
            return "81 F1 10 7E E0"
        return super()._kline(c)
