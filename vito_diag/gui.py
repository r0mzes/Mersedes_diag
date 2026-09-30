"""Простое окно для vito_diag: python -m vito_diag gui.

Окно не работает с машиной само — оно запускает те же команды, что и командная строка
(python -m vito_diag ...), и показывает их вывод. Одновременно выполняется одна команда:
COM-порт может открыть только одна программа.

Кнопок стирания ошибок и других запросов, меняющих данные в блоках, здесь нет намеренно.
"""

import glob
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ROOT = Path(__file__).resolve().parent.parent
_NOISE = re.compile(r"^WARNING:pint\.util")


def build_args(action: str, port: str = "", baudrate: str = "", line: str = "7",
               block: str = "", demo: bool = False) -> list:
    """Аргументы python -m vito_diag для кнопки окна."""
    common = []
    if demo:
        common.append("--demo")
    else:
        if port:
            common += ["--port", port]
        if baudrate:
            common += ["--baudrate", baudrate]
    if action == "scan":
        return ["scan"] + common
    if action == "live":
        return ["live"] + common + ["--interval", "1"]
    if action == "scan-all":
        return ["ecu", "scan-all"] + common + ["--line", line]
    if action == "read":
        block = block.strip()
        if not block:
            raise ValueError("Укажите блок: CAN «7E1:7E9» или адрес K-line «12»")
        key = "--can" if ":" in block or len(block) == 3 else "--kline"
        return ["ecu", "read"] + common + [key, block, "--line", line]
    raise ValueError(f"неизвестное действие {action}")


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [(p.device, p.description) for p in lp.comports()]


def bridge_exchange(port: str, lines, wait: float = 2.5) -> str:
    """Отправить строки в мост ESP32 (или адаптер) и вернуть всё, что пришло в ответ."""
    from vito_diag.elm import open_serial

    ser = open_serial(port, baudrate=38400, timeout=0.2)
    out = []
    try:
        for line, pause in lines:
            ser.write((line + ("\n" if line.startswith("~") else "\r")).encode("ascii"))
            end = time.time() + (pause or wait)
            buf = b""
            while time.time() < end:
                buf += ser.read(256)
            out.append(f">> {line}\n" + buf.decode("ascii", errors="replace").replace("\r", "\n").strip())
    finally:
        ser.close()
    return "\n".join(out)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.proc = None
        self.busy = False
        self.queue = queue.Queue()
        root.title("vito_diag — диагностика Mercedes Vito")
        root.geometry("900x700")
        root.minsize(720, 480)

        self.port = tk.StringVar()
        self.baud = tk.StringVar(value="38400")
        self.demo = tk.BooleanVar(value=False)
        self.line = tk.StringVar(value="7")
        self.block = tk.StringVar(value="12")
        self.status = tk.StringVar(value="Готово")

        pad = {"padx": 6, "pady": 4}

        conn = ttk.LabelFrame(root, text="Подключение")
        conn.pack(fill="x", **pad)
        ttk.Label(conn, text="Порт:").grid(row=0, column=0, sticky="w", **pad)
        self.port_box = ttk.Combobox(conn, textvariable=self.port, width=40)
        self.port_box.grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(conn, text="Обновить", command=self.refresh_ports).grid(row=0, column=2, **pad)
        ttk.Label(conn, text="Скорость:").grid(row=0, column=3, sticky="e", **pad)
        ttk.Entry(conn, textvariable=self.baud, width=8).grid(row=0, column=4, **pad)
        ttk.Checkbutton(conn, text="Демо (без машины)", variable=self.demo).grid(row=0, column=5, **pad)
        conn.columnconfigure(1, weight=1)

        bridge = ttk.LabelFrame(root, text="Мост ESP32 и адаптер")
        bridge.pack(fill="x", **pad)
        self.buttons = []
        self._button(bridge, "Проверить связь", self.check_link)
        self._button(bridge, "Переподключить адаптер", self.reconnect)
        ttk.Label(bridge, text="Если «connect failed» — выньте адаптер на 5–10 с и вставьте обратно."
                  ).pack(side="left", **pad)

        act = ttk.LabelFrame(root, text="Диагностика (только чтение)")
        act.pack(fill="x", **pad)
        row1 = ttk.Frame(act)
        row1.pack(fill="x")
        self._button(row1, "Скан двигателя (OBD-II)", lambda: self.run("scan"))
        self._button(row1, "Найти все блоки", lambda: self.run("scan-all"))
        self._button(row1, "Живые параметры", lambda: self.run("live"))
        ttk.Label(row1, text="Контакт K-line:").pack(side="left", **pad)
        ttk.Combobox(row1, textvariable=self.line, values=["7", "8", "9", "11"], width=4,
                     state="readonly").pack(side="left", **pad)
        row2 = ttk.Frame(act)
        row2.pack(fill="x")
        ttk.Label(row2, text="Блок:").pack(side="left", **pad)
        ttk.Combobox(row2, textvariable=self.block, width=12,
                     values=["12", "7E1:7E9"]).pack(side="left", **pad)
        self._button(row2, "Прочитать блок", lambda: self.run("read"))
        ttk.Label(row2, text="12 — двигатель (K-line), 7E1:7E9 — АКПП (CAN)").pack(side="left", **pad)

        tools = ttk.Frame(root)
        tools.pack(fill="x", **pad)
        self.stop_btn = ttk.Button(tools, text="Остановить", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", **pad)
        ttk.Button(tools, text="Открыть последний отчёт", command=self.open_report).pack(side="left", **pad)
        ttk.Button(tools, text="Папка логов", command=lambda: self._open(ROOT / "logs")).pack(side="left", **pad)
        ttk.Button(tools, text="Очистить окно", command=lambda: self.out.delete("1.0", "end")).pack(side="left", **pad)

        self.out = ScrolledText(root, wrap="word", font=("Consolas", 10))
        self.out.pack(fill="both", expand=True, **pad)
        ttk.Label(root, textvariable=self.status, anchor="w").pack(fill="x", padx=6, pady=(0, 4))

        self.refresh_ports()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._drain)

    # ---- вспомогательное --------------------------------------------------

    def _button(self, parent, text, command):
        b = ttk.Button(parent, text=text, command=command)
        b.pack(side="left", padx=6, pady=4)
        self.buttons.append(b)
        return b

    def _set_busy(self, busy: bool, text: str = "Готово"):
        self.busy = busy
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy and self.proc else "disabled")
        self.status.set(text)

    def write(self, text: str):
        """Вывод с учётом '\\r' (строка прогресса перезаписывается, а не копится)."""
        parts = text.split("\r")
        for i, part in enumerate(parts):
            if i:
                self.out.delete("end-1c linestart", "end-1c")
            self.out.insert("end", part)
        self.out.see("end")

    def _drain(self):
        try:
            while True:
                kind, data = self.queue.get_nowait()
                if kind == "text":
                    self.write(data)
                elif kind == "done":
                    self.proc = None
                    self._set_busy(False, data)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _port(self) -> str:
        return self.port.get().split(" ")[0].strip()

    @staticmethod
    def _open(path: Path):
        if not path.suffix:  # папка — создать, если её ещё нет
            path.mkdir(exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(path)])

    # ---- действия ---------------------------------------------------------

    def refresh_ports(self):
        ports = list_ports()
        self.port_box["values"] = [f"{d}  {desc}" for d, desc in ports]
        # Мост ESP32 и старый адаптер — FTDI («USB Serial Port»); COM3 на этом ноутбуке — Intel AMT.
        guess = next((d for d, desc in ports if "USB Serial" in desc or "FTDI" in desc), "")
        if guess and not self._port():
            self.port.set(guess)

    def _need_port(self) -> bool:
        if self.demo.get() or self._port():
            return True
        messagebox.showwarning("Порт", "Выберите COM-порт адаптера (кнопка «Обновить»).")
        return False

    def run(self, action: str):
        if self.busy or not self._need_port():
            return
        try:
            args = build_args(action, self._port(), self.baud.get().strip(), self.line.get(),
                              self.block.get(), self.demo.get())
        except ValueError as e:
            messagebox.showwarning("vito_diag", str(e))
            return
        cmd = [sys.executable, "-u", "-m", "vito_diag"] + args
        self.write(f"\n$ python -m vito_diag {' '.join(args)}\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     env=env, creationflags=flags)
        self._set_busy(True, "Выполняется: " + " ".join(args[:2]))
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        pending = ""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                break
            # В Windows строки кончаются на \r\n — одиночный \r оставляем только для прогресса.
            pending = (pending + chunk.decode("utf-8", errors="replace")).replace("\r\n", "\n")
            *lines, pending = pending.split("\n")
            lines = [l for l in lines if not _NOISE.match(l)]
            if lines:
                self.queue.put(("text", "\n".join(lines) + "\n"))
            if pending and "\r" in pending:  # строка прогресса без перевода строки
                self.queue.put(("text", pending))
                pending = ""
        if pending and not _NOISE.match(pending):
            self.queue.put(("text", pending + "\n"))
        code = proc.wait()
        self.queue.put(("done", "Готово" if code == 0 else f"Завершено с кодом {code}"))

    def _bridge(self, title: str, job):
        """Выполнить job(port) -> текст в фоне: общение с мостом напрямую, без vito_diag."""
        if self.busy or not self._need_port():
            return
        if self.demo.get():
            messagebox.showinfo("Демо", "В демо-режиме адаптер не используется.")
            return
        port = self._port()
        self.write(f"\n# {title} ({port})\n")
        self._set_busy(True, title + "...")

        def work():
            try:
                self.queue.put(("text", job(port) + "\n"))
                self.queue.put(("done", "Готово"))
            except Exception as e:  # порт занят, не найден и т. п.
                self.queue.put(("text", f"ОШИБКА: {e}\n"))
                self.queue.put(("done", "Ошибка связи"))

        threading.Thread(target=work, daemon=True).start()

    def check_link(self):
        self._bridge("Проверка связи",
                     lambda port: bridge_exchange(port, [("~STATE", 2), ("ATI", 2), ("AT RV", 2)]))

    def reconnect(self):
        def job(port):
            # ~USE нужен адрес адаптера — берём сохранённый в мосте из ~STATE.
            state = bridge_exchange(port, [("~STATE", 2)])
            m = re.search(r"addr=([0-9a-fA-F:]{17})", state)
            if not m:
                return state + "\nАдрес адаптера в мосте не сохранён."
            return bridge_exchange(port, [(f"~USE {m.group(1)}", 15), ("AT RV", 2)])

        self._bridge("Переподключение адаптера", job)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.write("\n[остановлено]\n")

    def open_report(self):
        files = sorted(glob.glob(str(ROOT / "reports" / "*.html")), key=os.path.getmtime)
        if not files:
            messagebox.showinfo("Отчёты", "Отчётов пока нет — сначала сделайте «Скан двигателя».")
            return
        self._open(Path(files[-1]))

    def on_close(self):
        self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
