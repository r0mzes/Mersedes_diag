"""Простое окно для vito_diag: python -m vito_diag gui.

Окно не работает с машиной само — оно запускает те же команды, что и командная строка
(python -m vito_diag ...), и показывает их вывод. Одновременно выполняется одна команда:
COM-порт может открыть только одна программа.

Кнопок стирания ошибок и других запросов, меняющих данные в блоках, здесь нет намеренно.
"""

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


ALL_LINES = "7,8,9,11"


def build_args(action: str, port: str = "", baudrate: str = "", line: str = "7",
               block: str = "", demo: bool = False, switch: bool = False) -> list:
    """Аргументы python -m vito_diag для кнопки окна.

    switch — авто-переключатель линий на HC-06, через тот же мост ESP32 (--switch bt)."""
    if line == ALL_LINES and not (switch and action == "scan-all"):
        raise ValueError("Все линии сразу — только «Найти все блоки» с включённым авто-переключателем")
    common = []
    if demo:
        common += ["--demo", "--no-save"]  # демо-отчёты не должны смешиваться с реальными
    else:
        if port:
            common += ["--port", port]
        if baudrate:
            common += ["--baudrate", baudrate]
    if action == "scan":
        return ["scan"] + common
    if action == "live":
        return ["live"] + common + ["--interval", "1"]
    ecu_sw = ["--switch", "bt"] if switch and not demo else []
    if action == "switch-meas":
        if demo or not port:
            raise ValueError("Замер — только с реальным мостом: выберите порт и снимите «Демо»")
        return ["switch", "meas", "--switch", "bt", "--port", port]
    if action == "scan-all":
        return ["ecu", "scan-all"] + common + ecu_sw + ["--line", line]
    if action == "read":
        block = block.strip()
        if not block:
            raise ValueError("Укажите блок: CAN «7E1:7E9» или адрес K-line «12»")
        key = "--can" if ":" in block or len(block) == 3 else "--kline"
        return ["ecu", "read"] + common + ecu_sw + [key, block, "--line", line]
    raise ValueError(f"неизвестное действие {action}")


def _when(created: str) -> str:
    """'2026-09-30T21:26:56' -> '30.09 21:26'."""
    date, _, clock = created.partition("T")
    return f"{date[8:10]}.{date[5:7]} {clock[:5]}"


def describe_saved(path: Path):
    """Сохранённый результат -> (строка для списка, демо ли это). None — не наш файл."""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if path.name.startswith("vito_"):
        demo = data.get("vehicle", {}).get("Порт") == "симулятор"
        codes = [d["code"] for d in data.get("dtcs", [])]
        title = (f"{_when(data.get('created', ''))}  Скан двигателя (OBD-II) — ошибок: {len(codes)}"
                 + (f" [{', '.join(codes)}]" if codes else "") + ("   (ДЕМО)" if demo else ""))
        return title, demo
    if path.name.startswith("modules_"):
        mods = data.get("modules", [])
        codes = sorted({d["code"] for m in mods for d in m.get("dtcs", [])})
        title = (f"{_when(data.get('created', ''))}  Опрос блоков — блоков: {len(mods)}, "
                 f"ошибок: {len(codes)}" + (f" [{', '.join(codes[:6])}{' …' if len(codes) > 6 else ''}]"
                                            if codes else ""))
        if len(mods) > 50:
            title += "   (ложный результат: клон игнорировал адрес)"
        return title, False
    return None


def format_saved(path: Path) -> str:
    """Текст сохранённого результата для окна вывода."""
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    out = [f"=== {path.name} ==="]
    if path.name.startswith("vito_"):
        out += [f" {k:<24} {v}" for k, v in data.get("vehicle", {}).items()]
        out.append(f" ИТОГ: {data.get('verdict', '')}")
        for d in data.get("dtcs", []):
            out.append(f"  {d['code']:<8} {d['description']}  ({d.get('source', '')})")
            if d.get("advice"):
                out.append(f"           → {d['advice']}")
        for f in data.get("findings", []):
            out.append(f" • {f['title']} ({', '.join(f.get('codes', []))}): {f.get('advice', '')}")
        return "\n".join(out)
    mods = data.get("modules", [])
    if len(mods) > 50:
        out.append(f" Блоков: {len(mods)} — это ложный результат старой версии (клон ELM327 "
                   "игнорировал адрес запроса). Показаны первые 3.")
        mods = mods[:3]
    for m in mods:
        if m["bus"] == "can":
            name = f"CAN {m['address']}/{m['reply']}"
        else:
            name = f"K-line (конт. {m.get('line', '7')}) {m['address']}"
        out.append(f"\n■ {name}  протокол: {m.get('protocol') or '?'}")
        if m.get("part_number"):
            out.append(f"    номер детали: {m['part_number']}")
        for note in m.get("notes", []):
            out.append(f"    ! {note}")
        if m.get("error"):
            out.append(f"    ошибки не прочитаны: {m['error']}")
        elif not m.get("dtcs"):
            out.append("    ошибок нет")
        for d in m.get("dtcs", []):
            extra = d.get("extra", {})
            out.append(f"    {d['code']} (статус {extra.get('status_byte', '?')}: "
                       f"{', '.join(extra.get('status', []))}) — {d['description']}")
    return "\n".join(out)


def saved_results(reports_dir: Path):
    """[(путь, строка, демо)] — новые сверху. HTML не берём: у каждого отчёта есть JSON."""
    items = []
    # Сортируем по времени из имени (vito_2026-09-30_21-26-56.json): у скопированных файлов и
    # после git clone время изменения файла не совпадает со временем замера.
    for p in sorted(reports_dir.glob("*.json"), key=lambda p: p.stem.split("_", 1)[-1], reverse=True):
        info = describe_saved(p)
        if info:
            items.append((p, *info))
    return items


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
        self.switch = tk.BooleanVar(value=False)
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
        ttk.Combobox(row1, textvariable=self.line, values=["7", "8", "9", "11", ALL_LINES], width=8,
                     state="readonly").pack(side="left", **pad)
        row2 = ttk.Frame(act)
        row2.pack(fill="x")
        ttk.Label(row2, text="Блок:").pack(side="left", **pad)
        ttk.Combobox(row2, textvariable=self.block, width=12,
                     values=["12", "7E1:7E9"]).pack(side="left", **pad)
        self._button(row2, "Прочитать блок", lambda: self.run("read"))
        ttk.Label(row2, text="12 — двигатель (K-line), 7E1:7E9 — АКПП (CAN)").pack(side="left", **pad)
        row3 = ttk.Frame(act)
        row3.pack(fill="x")
        ttk.Checkbutton(row3, text="Авто-переключатель линий (Arduino, через мост)",
                        variable=self.switch).pack(side="left", **pad)
        self._button(row3, "Замер контактов", lambda: self.run("switch-meas"))
        ttk.Label(row3, text="смена линии ~50 с; без делителей замер — шум").pack(side="left", **pad)

        tools = ttk.Frame(root)
        tools.pack(fill="x", **pad)
        self.stop_btn = ttk.Button(tools, text="Остановить", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", **pad)
        ttk.Button(tools, text="Сохранённые результаты", command=self.show_saved).pack(side="left", **pad)
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
                              self.block.get(), self.demo.get(), self.switch.get())
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
            from vito_diag.switch import DEFAULT_ELM, DEFAULT_HC06

            addr = m.group(1)
            if addr.lower() == DEFAULT_HC06:  # прерванная работа оставила мост на переключателе
                addr = DEFAULT_ELM
            # первое подключение после смены устройства у моста не проходит — сначала ~SCAN
            return bridge_exchange(port, [("~SCAN", 13), (f"~USE {addr}", 15), ("AT RV", 2)])

        self._bridge("Переподключение адаптера", job)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.write("\n[остановлено]\n")

    def open_report(self):
        """Последний HTML-отчёт скана двигателя с реальной машины (демо пропускаем)."""
        for path, _, demo in saved_results(ROOT / "reports"):
            html = path.with_suffix(".html")
            if path.name.startswith("vito_") and not demo and html.exists():
                self._open(html)
                return
        messagebox.showinfo("Отчёты", "Отчётов с машины пока нет — сначала сделайте «Скан двигателя».")

    def show_saved(self):
        """Список сохранённых результатов; выбранный показывается в окне вывода."""
        items = saved_results(ROOT / "reports")
        if not items:
            messagebox.showinfo("Результаты", "Сохранённых результатов пока нет.")
            return
        win = tk.Toplevel(self.root)
        win.title("Сохранённые результаты")
        win.geometry("760x360")
        box = tk.Listbox(win, font=("Consolas", 10), activestyle="dotbox")
        box.pack(fill="both", expand=True, padx=6, pady=6)
        for _, title, demo in items:
            box.insert("end", title)
            if demo:
                box.itemconfigure("end", foreground="gray")
        box.selection_set(0)

        def show(_event=None):
            sel = box.curselection()
            if sel:
                self.write("\n" + format_saved(items[sel[0]][0]) + "\n")

        def open_html():
            sel = box.curselection()
            html = items[sel[0]][0].with_suffix(".html") if sel else None
            if html and html.exists():
                self._open(html)
            else:
                messagebox.showinfo("HTML", "HTML-отчёт есть только у скана двигателя.", parent=win)

        box.bind("<Double-Button-1>", show)
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(bar, text="Показать в окне", command=show).pack(side="left", padx=4)
        ttk.Button(bar, text="Открыть HTML", command=open_html).pack(side="left", padx=4)
        ttk.Label(bar, text="Двойной щелчок — показать. Серым — демо.").pack(side="left", padx=8)

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
