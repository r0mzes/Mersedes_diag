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


def cmd_uds(args):
    if args.demo:
        print("Режим uds работает только с реальным адаптером.")
        return 1
    if not args.port:
        print("Укажите порт: --port COM3 (Windows) / /dev/ttyUSB0 (Linux) / socket://192.168.0.10:35000 (Wi-Fi)")
        return 1
    from vito_diag.uds import KNOWN_ECUS, ElmError, ElmUds

    elm = ElmUds(args.port, baudrate=args.baudrate or 38400)
    try:
        if args.scan:
            print("Поиск блоков на CAN (1-3 минуты)...")
            found = elm.scan(progress=lambda tx: print(f"\r  0x{tx:03X}", end="", flush=True))
            print()
            if not found:
                print("Ни один блок не ответил на прямой адрес.")
            for tx, rx in found:
                print(f"  запрос 0x{tx:03X} -> ответ 0x{rx:03X}   (python -m vito_diag uds --port {args.port} --tx {tx:X} --rx {rx:X})")
            return 0

        if args.tx:
            targets = [(int(args.tx, 16), int(args.rx or f"{int(args.tx, 16) + 8:X}", 16), f"0x{args.tx.upper()}")]
        else:
            targets = [v for v in KNOWN_ECUS.values()]

        dtcs = []
        for tx, rx, name in targets:
            print(f"Блок {name} (0x{tx:03X}/0x{rx:03X})... ", end="", flush=True)
            try:
                found = elm.read_dtcs(tx, rx, name)
                print(f"ошибок: {len(found)}")
                dtcs.extend(found)
            except ElmError as e:
                print(f"нет ответа ({e})")
    finally:
        elm.close()

    analysis = analyze([], [], extra_dtcs=dtcs)
    info = {"Порт": args.port, "Режим": "UDS 0x19 0x02 (ошибки блоков)"}
    print_report(info, analysis)
    for d in dtcs:
        print(f"  {d.code} [{d.ecu}] тип сбоя {d.extra['failure_type']}, статус: {', '.join(d.extra['status']) or '-'}")
    if not args.no_save:
        paths = save_report(info, analysis, args.reports_dir)
        print(f"Отчёт сохранён: {paths['html']}")
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

    u = sub.add_parser("uds", parents=[common], help="(эксперимент) ошибки блоков по UDS: КПП и др.")
    u.add_argument("--tx", help="CAN ID запроса, hex (например 7E1)")
    u.add_argument("--rx", help="CAN ID ответа, hex (по умолчанию tx+8)")
    u.add_argument("--scan", action="store_true", help="найти отвечающие блоки")
    u.set_defaults(func=cmd_uds)

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
