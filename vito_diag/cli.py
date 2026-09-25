"""Командная строка: python -m vito_diag <команда>."""

import argparse
import csv
import logging
import sys
import time
from datetime import datetime

from vito_diag import __version__
from vito_diag.analyzer import analyze, check_live
from vito_diag.report import print_report, save_report
from vito_diag.sources import SimulatedSource


def _setup_console():
    # Консоль Windows по умолчанию может не показать кириллицу.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def open_source(args):
    if args.demo:
        return SimulatedSource(seed=1)
    from vito_diag.sources import ObdSource

    print("Подключение к адаптеру... (зажигание должно быть включено)")
    return ObdSource(port=args.port, baudrate=args.baudrate, protocol=args.protocol)


def cmd_ports(args):
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    if not ports:
        print("Последовательные порты не найдены.")
        print("USB-адаптер: проверьте драйвер (CH340 / FTDI / CP210x).")
        print("Bluetooth-адаптер: сначала выполните сопряжение (PIN обычно 1234 или 0000).")
        return 1
    print("Найденные порты:")
    for p in ports:
        print(f"  {p.device:<22} {p.description}  [{p.hwid}]")
    print("\nИспользуйте: python -m vito_diag scan --port <порт>")
    return 0


def cmd_scan(args):
    src = open_source(args)
    try:
        info = src.info()
        stored = src.read_dtcs()
        pending = src.read_pending_dtcs()
        live = src.read_live() if not args.no_live else {}
    finally:
        src.close()
    analysis = analyze(stored, pending, live)
    print_report(info, analysis)
    if not args.no_save:
        paths = save_report(info, analysis, args.reports_dir)
        print(f"Отчёт сохранён: {paths['html']}  и  {paths['json']}")
    return 0


def cmd_clear(args):
    if not args.yes:
        print("ВНИМАНИЕ: будут стёрты ошибки и данные стоп-кадра в ЭБУ двигателя,")
        print("а также сброшены готовности систем самодиагностики.")
        print("Сначала сохраните отчёт командой scan! Двигатель должен быть ЗАГЛУШЕН.")
        if input("Стереть ошибки? Введите 'да': ").strip().lower() not in ("да", "yes", "y"):
            print("Отменено.")
            return 1
    src = open_source(args)
    try:
        ok = src.clear_dtcs()
    finally:
        src.close()
    print("Ошибки стёрты." if ok else "Блок не подтвердил стирание.")
    return 0 if ok else 2


def cmd_live(args):
    src = open_source(args)
    writer = None
    log_file = None
    try:
        if args.log:
            log_file = open(args.log, "w", newline="", encoding="utf-8")
        print("Чтение параметров. Ctrl+C — остановить.\n")
        while True:
            live = src.read_live()
            stamp = datetime.now().strftime("%H:%M:%S")
            print(f"--- {stamp} ---")
            for label, value, unit in live.values():
                shown = unit if value is None else f"{value:g} {unit}"
                print(f"  {label:<40} {shown}")
            for w in check_live(live):
                print(f"  ! {w}")
            if log_file:
                if writer is None:
                    writer = csv.writer(log_file, delimiter=";")
                    writer.writerow(["time"] + [f"{lbl} ({u})" for lbl, _, u in live.values()])
                writer.writerow([stamp] + ["" if v is None else v for _, v, _ in live.values()])
                log_file.flush()
            if args.count:
                args.count -= 1
                if args.count == 0:
                    break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        src.close()
        if log_file:
            log_file.close()
            print(f"Лог сохранён: {args.log}")
    return 0


def _parse_addr_list(text):
    """'10,18,28-2F' -> [0x10, 0x18, 0x28..0x2F] (hex)."""
    result = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            result.extend(range(int(a, 16), int(b, 16) + 1))
        elif part:
            result.append(int(part, 16))
    return result


def _module_from_args(args):
    from vito_diag.elm import Module

    if args.can:
        tx, _, rx = args.can.partition(":")
        tx_id = int(tx, 16)
        return Module("can", tx_id, int(rx, 16) if rx else tx_id + 8)
    if args.kline:
        return Module("kline", int(args.kline, 16), line=args.line)
    raise ValueError("Укажите блок: --can 7E0[:7E8] или --kline 10")


def _print_module(m):
    print(f"\n■ {m.name}  протокол: {m.protocol or '?'}")
    if m.part_number:
        print(f"    номер детали: {m.part_number}")
    if m.ident_text:
        print(f"    идентификация: {m.ident_text}")
    for req, raw in m.ident_raw.items():
        print(f"    [{req}] {raw}")
    if m.error:
        print(f"    ошибки не прочитаны: {m.error}")
    elif not m.dtcs:
        print("    ошибок нет")
    for d in m.dtcs:
        print(f"    {d.code} (сырой {d.extra['raw']}, статус {d.extra['status_byte']}: "
              f"{', '.join(d.extra['status'])}) — {d.description}")


def _save_modules(modules, args, extra=None):
    import json
    from pathlib import Path

    out = Path(args.reports_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"modules_{datetime.now():%Y-%m-%d_%H-%M-%S}.json"
    data = {"created": datetime.now().isoformat(timespec="seconds"),
            "modules": [m.to_dict() for m in modules], **(extra or {})}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def cmd_ecu(args):
    if args.demo:
        print("Команда ecu работает только с реальным адаптером.")
        return 1
    if not args.port:
        print("Укажите порт: --port COM3 (Windows) / /dev/ttyUSB0 (Linux)")
        return 1
    from vito_diag.elm import ElmLink
    from vito_diag.protocol import DiagError, hex_bytes, parse_hex_request

    link = ElmLink(args.port, baudrate=args.baudrate or 38400, unsafe=args.unsafe)
    print(f"Адаптер: {link.version.splitlines()[-1] if link.version else '?'};  "
          f"напряжение: {link.voltage()};  лог обмена: {link.log_path}")
    can_progress = (lambda a: print(f"\r  CAN ID 0x{a:03X}", end="", flush=True))
    kline_progress = (lambda a: print(f"\r  адрес K-line 0x{a:02X}", end="", flush=True))
    modules = []
    try:
        if args.action == "monitor":
            ids = link.monitor_can(args.seconds)
            if not ids:
                print("На CAN (контакты 6/14) тишина — это нормально для отдельной диагностической шины.")
            for cid, n in sorted(ids.items()):
                print(f"  0x{cid:03X}: {n} кадров")
            return 0

        if args.action == "raw":
            m = _module_from_args(args)
            payload = link.request(m, parse_hex_request(args.request))
            print(hex_bytes(payload))
            return 0

        if args.action == "read":
            modules = [_module_from_args(args)]
        if args.action in ("scan-can", "scan-all"):
            print("Поиск блоков на CAN (контакты 6/14). Займёт несколько минут...")
            found = link.scan_can(int(args.start, 16), int(args.end, 16), can_progress)
            print(f"\n  найдено на CAN: {len(found)}")
            modules += found
        if args.action in ("scan-kline", "scan-all"):
            addrs = _parse_addr_list(args.addrs) if args.addrs else range(0x01, 0xF0)
            print(f"Поиск блоков на K-line (контакт машины {args.line}). Это долго — до 10-20 минут...")
            found = link.scan_kline(addrs, line=args.line, slow=args.slow, progress=kline_progress)
            print(f"\n  найдено на K-line: {len(found)}")
            modules += found

        for m in modules:
            try:
                link.identify(m)
                link.read_dtcs(m)
            except DiagError as e:
                m.error = str(e)
            _print_module(m)
    finally:
        link.close()

    if modules and not args.no_save:
        path = _save_modules(modules, args, {"log": str(link.log_path), "kline_pin": args.line})
        print(f"\nРезультат: {path}\nЛог обмена: {link.log_path}")
    all_dtcs = [d for m in modules for d in m.dtcs]
    if all_dtcs:
        print_report({"Режим": "опрос блоков (ELM327 напрямую)"}, analyze([], [], extra_dtcs=all_dtcs))
    return 0


def cmd_lookup(args):
    from vito_diag.dtc import SEVERITY_RU, lookup

    for code in args.codes:
        d = lookup(code)
        print(f"{d.code}: {d.description}")
        print(f"   Система: {d.system}; серьёзность: {SEVERITY_RU.get(d.severity)}")
        print(f"   Что проверить: {d.advice}\n")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="vito_diag",
        description="Диагностика Mercedes Vito 4x4 через адаптер ELM327 (OBD-II).",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true", help="подробный лог обмена с адаптером")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", help="COM3 / /dev/ttyUSB0 / /dev/rfcomm0 / socket://192.168.0.10:35000 (по умолчанию — автопоиск)")
    common.add_argument("--baudrate", type=int, help="скорость порта (по умолчанию — автоподбор)")
    common.add_argument("--protocol", help="протокол OBD ELM327: 6 = CAN 11bit/500k (по умолчанию — авто)")
    common.add_argument("--demo", action="store_true", help="демо-режим без автомобиля")
    common.add_argument("--reports-dir", default="reports", help="папка для отчётов")
    common.add_argument("--no-save", action="store_true", help="не сохранять отчёт")

    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("ports", help="показать доступные COM-порты").set_defaults(func=cmd_ports)

    s = sub.add_parser("scan", parents=[common], help="считать и проанализировать ошибки")
    s.add_argument("--no-live", action="store_true", help="не читать текущие параметры")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("clear", parents=[common], help="стереть ошибки в ЭБУ двигателя")
    c.add_argument("-y", "--yes", action="store_true", help="без подтверждения")
    c.set_defaults(func=cmd_clear)

    l = sub.add_parser("live", parents=[common], help="текущие параметры в реальном времени")
    l.add_argument("--interval", type=float, default=1.0, help="период опроса, с")
    l.add_argument("--count", type=int, default=0, help="сколько циклов (0 — бесконечно)")
    l.add_argument("--log", help="записывать в CSV-файл")
    l.set_defaults(func=cmd_live)

    e = sub.add_parser("ecu", parents=[common],
                       help="опрос блоков напрямую через ELM327: поиск, идентификация, ошибки")
    e.add_argument("action", choices=["monitor", "scan-can", "scan-kline", "scan-all", "read", "raw"],
                   help="monitor — послушать CAN; scan-* — найти блоки; read — прочитать блок; "
                        "raw — отправить свой запрос")
    e.add_argument("request", nargs="?", help="для raw: запрос в hex, например '1A 86'")
    e.add_argument("--can", help="блок на CAN: ID запроса[:ID ответа], hex (например 7E0:7E8)")
    e.add_argument("--kline", help="блок на K-line: адрес, hex (например 10)")
    e.add_argument("--line", default="7",
                   help="какой контакт машины сейчас выбран переключателем (7, 8, 9, 11) — для отчёта")
    e.add_argument("--start", default="400", help="scan-can: начальный CAN ID (hex)")
    e.add_argument("--end", default="7FF", help="scan-can: конечный CAN ID (hex)")
    e.add_argument("--addrs", help="scan-kline: адреса, hex (например '01-3F,58')")
    e.add_argument("--slow", action="store_true", help="scan-kline: пробовать и медленную 5-бод инициализацию")
    e.add_argument("--seconds", type=float, default=5.0, help="monitor: сколько секунд слушать")
    e.add_argument("--unsafe", action="store_true",
                   help="разрешить запросы, меняющие данные в блоках (НЕ использовать без необходимости)")
    e.set_defaults(func=cmd_ecu)

    k = sub.add_parser("lookup", help="расшифровать код(ы) без подключения")
    k.add_argument("codes", nargs="+")
    k.set_defaults(func=cmd_lookup)
    return p


def main(argv=None):
    _setup_console()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.verbose:
        import obd
        obd.logger.setLevel(obd.logging.DEBUG)
    try:
        return args.func(args)
    except ConnectionError as e:
        print(f"ОШИБКА: {e}")
        return 2
    except Exception as e:  # понятное сообщение вместо трассировки
        if args.verbose:
            raise
        print(f"ОШИБКА: {e.__class__.__name__}: {e}  (для подробностей запустите с -v)")
        return 2
