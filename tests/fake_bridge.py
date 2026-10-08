"""Эмулятор моста ESP32: одно соединение Bluetooth — либо ELM327, либо HC-06 переключателя."""

from tests.fake_elm import FakeElm
from tests.fake_switch import FakeSwitch

ELM = "01:2d:a1:86:68:c0"
HC06 = "98:d3:41:00:0f:55"


class FakeBridge:
    def __init__(self, fail_first_use=True):
        self.elm = FakeElm()
        self.sw = FakeSwitch()
        self.addr = ELM
        self.connected = True
        self.out = b""
        self.uses = []
        self.fail_first_use = fail_first_use  # как настоящий мост: после смены устройства — до ~SCAN
        self._scanned = False

    @property
    def device(self):
        if not self.connected:
            return None
        return self.elm if self.addr == ELM else self.sw

    @property
    def in_waiting(self):
        self._pull()
        return len(self.out)

    def _pull(self):
        dev = self.device
        if dev is not None and dev.out:
            self.out += dev.out
            dev.out = b""

    def read(self, n=1):
        self._pull()
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def reset_input_buffer(self):
        self._pull()
        self.out = b""

    def close(self):
        pass

    def write(self, data):
        text = data.decode()
        if text.startswith("~"):
            self._bridge(text[1:].strip())
        elif self.device is not None:
            self.device.write(data)

    def _bridge(self, c):
        if c == "STATE":
            self.out += f"~STATE connected={int(self.connected)} addr={self.addr} pin=1234\n".encode()
        elif c == "SCAN":
            self._scanned = True
            self.out += f"~SCAN...\n~  {ELM}  OBD II  rssi=-55\n~  {HC06}  ?  rssi=-65\n~SCAN done, 2 found\n".encode()
        elif c.startswith("USE "):
            addr = c[4:].lower()
            self.uses.append(addr)
            changed = addr != self.addr
            self.addr = addr
            self.out += f"~connecting {addr} pin=1234\n".encode()
            if self.fail_first_use and changed and not self._scanned:
                self.connected = False
                self.out += b"~connect failed\n~DISCONNECTED\n"
            else:
                self.connected = True
                self._scanned = False
                self.out += b"~CONNECTED\n"
