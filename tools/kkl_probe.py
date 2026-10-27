"""Проверка связи кабелем KKL (FTDI) напрямую: быстрая инициализация ISO 14230 к «Январю», только чтение.
    python tools/kkl_probe.py COM9
Запросы: 81 (StartCommunication), 1A 80, 18 00 FF 00, 01 00, 21 01, 82 (StopCommunication)."""
import sys
import time

import serial

PORT = sys.argv[1]
ADDRS = [0x10, 0x11]
ALLOWED = {0x81, 0x82, 0x1A, 0x18, 0x01, 0x21, 0x3E}


def frame(tgt, data):
    b = [0x80 | len(data), tgt, 0xF1] + list(data)
    return bytes(b + [sum(b) & 0xFF])


def hx(b):
    return " ".join(f"{x:02X}" for x in b)


s = serial.Serial(PORT, 10400, timeout=0.05)
print("порт", PORT, "dtr", s.dtr, "rts", s.rts)


def read_for(sec):
    buf = b""
    end = time.time() + sec
    while time.time() < end:
        buf += s.read(256)
    return buf


def send(tgt, data, wait=0.6):
    assert data[0] in ALLOWED
    f = frame(tgt, data)
    s.reset_input_buffer()
    s.write(f)
    s.flush()
    raw = read_for(wait)
    echo, rest = raw[:len(f)], raw[len(f):]
    print(f"  >> {hx(f)}   эхо: {'да' if echo == f else hx(echo)}   ответ: {hx(rest) or '-'}")
    return rest


def fast_init(tgt):
    s.break_condition = False
    time.sleep(0.4)             # линия в покое (W5 > 300 мс)
    s.reset_input_buffer()
    s.break_condition = True    # TiniL 25 мс низкий уровень
    time.sleep(0.025)
    s.break_condition = False   # 25 мс высокий
    time.sleep(0.025)
    return send(tgt, [0x81], wait=0.4)


for addr in ADDRS:
    for attempt in range(2):
        print(f"адрес 0x{addr:02X}, попытка {attempt + 1}")
        r = fast_init(addr)
        if r:
            for req in ([0x1A, 0x80], [0x18, 0x00, 0xFF, 0x00], [0x01, 0x00], [0x21, 0x01]):
                time.sleep(0.06)
                send(addr, req, wait=1.0)
            time.sleep(0.06)
            send(addr, [0x82], wait=0.4)
            s.close()
            sys.exit(0)
        time.sleep(3)  # пауза перед повтором, чтобы блок вышел из ожидания
s.close()
print("ответа нет")
