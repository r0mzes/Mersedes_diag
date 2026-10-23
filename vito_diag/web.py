"""Веб-панель: python -m vito_diag web. Открывается в браузере ноутбука или телефона (по Wi-Fi).

Без сторонних библиотек: http.server + Server-Sent Events. Сервер только показывает данные
Poller (vito_diag.live); кнопок записи в блоки здесь нет и не будет.
"""
from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).parent / "data" / "panel.html"


def lan_addresses() -> list:
    """IP ноутбука в локальной сети — их набирать в телефоне."""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 9))  # пакет не уходит: так ОС лишь выбирает интерфейс
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def make_handler(poller):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # без строки в консоли на каждый запрос
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                body = json.dumps(poller.snapshot(history=True), ensure_ascii=False).encode()
                self._send(200, body, "application/json; charset=utf-8")
            elif path == "/api/stream":
                self._stream()
            else:
                self._send(404, b"not found", "text/plain")

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            version = -1
            try:
                while True:
                    new = poller.wait(version, timeout=1.0)
                    # Раз в секунду шлём и без изменений: обновляется «возраст» значений.
                    version = new
                    data = json.dumps(poller.snapshot(), ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

    return Handler


def serve(poller, host: str = "0.0.0.0", port: int = 8080, ready=None):
    server = ThreadingHTTPServer((host, port), make_handler(poller))
    server.daemon_threads = True
    if ready:
        ready(server)
    try:
        server.serve_forever()
    finally:
        server.server_close()
