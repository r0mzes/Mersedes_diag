"""Эмулятор прошивки firmware/obd_switch для тестов."""


class FakeSwitch:
    def __init__(self):
        self.out = b""
        self.sent = []
        self.line = "7"

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
        cmd = data.decode().strip().upper()
        self.sent.append(cmd)
        self.out += (self._answer(cmd) + "\r\n").encode()

    def _answer(self, c):
        if c == "ID":
            return "OBDSW 1"
        if c == "STATE":
            return f"STATE SEL {self.line} CAN STD PWR ON"
        if c == "RESET":
            self.line = "7"
            return "OK RESET"
        if c.startswith("SEL "):
            if c[4:] not in ("7", "8", "9", "11"):
                return "ERR SEL: only 7, 8, 9, 11"
            self.line = c[4:]
            return "OK " + c
        if c in ("PWR ON", "PWR OFF", "CAN STD", "CAN ALT"):
            return "OK " + c
        if c == "MEAS":
            return "MEAS 16:12.45:12.40:12.50 7:12.10:1.20:12.30 8:0.01:0.00:0.02 6:2.51:2.30:3.40"
        return "ERR unknown command"
