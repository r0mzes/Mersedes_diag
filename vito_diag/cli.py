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


def _format_ident(ident):
    names = (("hw", "железо"), ("sw", "ПО"), ("supplier", "поставщик"),
             ("diag_variant", "вариант диагностики"), ("produced", "выпущен"))
    return ", ".join(f"{label} {ident[key]}" for key, label in names if ident.get(key))


def _print_module(m):
    print(f"\n■ {m.name}  протокол: {m.protocol or '?'}")
    if m.title:
        print(f"    блок: {m.title}")
    if m.part_number:
        print(f"    номер детали: {m.part_number}")
    if m.ident:
        print("    " + _format_ident(m.ident))
    if m.ident_text:
        print(f"    идентификация: {m.ident_text}")
    for req, raw in m.ident_raw.items():
        print(f"    [{req}] {raw}")
    for note in m.notes:
        print(f"    ! {note}")
    if m.error:
        print(f"    ошибки не прочитаны: {m.error}")
    elif not m.dtcs:
        print("    ошибок нет")
    for d in m.dtcs:
        print(f"    {d.code} (сырой {d.extra['raw']}, статус {d.extra['status_byte']}: "
              f"{', '.join(d.extra['status'])}) — {d.description}")
        if d.extra.get("details_raw"):
            env = d.extra.get("details_env")
            print(f"        [17] {d.extra['details_raw']}" + (f"  (данные окружения: {env})" if env else ""))
        elif d.extra.get("details_error"):
            print(f"        [17] {d.extra['details_error']}")


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


def _read_modules(link, modules, can_link=None, details=False):
    from vito_diag.protocol import DiagError

    for m in modules:
        if m.bus == "can" and can_link is not None:
            _read_modules(can_link, [m], details=details)
            continue
        try:
            link.identify(m)
            link.read_dtcs(m)
            if details:
                link.read_dtc_details(m)
        except DiagError as e:
            m.error = str(e)
        _print_module(m)


def _live(args, link, can_link, sw, line):
    """ecu dump / ecu record: живые данные блока (служба 21, только чтение)."""
    import json
    from datetime import datetime
    from pathlib import Path

    from vito_diag.livedata import changing_bytes, dump_lids, record

    args.line = line
    m = _module_from_args(args)
    if m.bus == "kline":
        _select_line(link, sw, m.line)
    lk = can_link if m.bus == "can" else link
    out = Path(args.reports_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tag = f"{m.address:03X}" if m.bus == "can" else f"kl{m.address:02X}"
    if args.action == "dump":
        lids = _parse_addr_list(args.lids or "01-FF")
        found = dump_lids(lk, m, lids, lambda x: print(f"\r  LID 0x{x:02X}", end="", flush=True))
        print(f"\n  ответили LID: {len(found)}")
        for lid, data in found.items():
            print(f"    21 {lid:02X} -> {data}")
        path = out / f"live_{tag}_{stamp}.json"
        path.write_text(json.dumps({"module": m.name, "created": stamp, "lids":
                                    {f"{k:02X}": v for k, v in found.items()}},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nРезультат: {path}\nЛог обмена: {lk.log_path}")
        return 0
    if not args.lids:
        print("Для record укажите --lids (из ecu dump), например --lids 01,02,05")
        return 1
    path = out / f"rec_{tag}_{stamp}.csv"
    print(f"Запись {args.seconds:.0f} с: {m.name}, LID {args.lids}. Ctrl+C — остановить раньше.")
    try:
        rows = record(lk, m, _parse_addr_list(args.lids), args.seconds, path)
    except KeyboardInterrupt:
        print("\nОстановлено.")
        return 0
    print(f"  опросов: {len(rows)}")
    for c in changing_bytes(rows):
        print(f"    21 {c['lid']:02X} байт {c['index']}: {c['min']}..{c['max']} ({c['values']} значений)")
    print(f"\nЗапись: {path}\nЛог обмена: {lk.log_path}")
    return 0


def _select_line(link, sw, line):
    """Перевести переключатель на контакт машины line (между запросами, не во время)."""
    if sw is None:
        return
    if link.mode == "kline":
        link.cmd("ATPC")
    link.mode = None
    link.target = None
    sw.select(line)
    print(f"Переключатель: контакт машины {line} -> контакт 7 адаптера")
    time.sleep(0.3)


def _print_lada_head(rep):
    """Блок, идентификация и ошибки «Января» — печатаются до живых данных."""
    print(f"\n\n■ Блок двигателя, K-line адрес 0x{rep.address:02X}")
    if rep.ecu_number:
        print(f"    номер блока: {rep.ecu_number}")
    if rep.vin:
        print(f"    VIN: {rep.vin}")
    if rep.ident_text:
        print(f"    идентификация: {rep.ident_text}")
    for req, raw in rep.ident_raw.items():
        print(f"    [{req}] {raw}")
    if rep.dtc_error:
        print(f"    ошибки не прочитаны: {rep.dtc_error}")
    elif not rep.dtcs:
        print("    ошибок нет")
    for d in rep.dtcs:
        print(f"    {d.code} (сырой {d.extra['raw']}, статус {d.extra['status_byte']}: "
              f"{', '.join(d.extra['status'])}) — {d.description}")
    for note in rep.notes:
        print(f"    ! {note}")
    if rep.fuel_system:
        print(f"    топливная система: {rep.fuel_system}")
    print(flush=True)


def cmd_lada(args):
    """ВАЗ «Январь»: опознание, ошибки, живые данные OBD-II и подсказки по смеси."""
    import json
    from pathlib import Path

    from vito_diag import january
    from vito_diag.elm import ElmLink
    from vito_diag.protocol import DiagError

    hub = None
    if args.hub:
        from vito_diag.hub import Hub

        hub = Hub(args.hub)
    try:
        link = ElmLink(args.port, baudrate=args.baudrate)
    except Exception:
        if hub:
            hub.close()
        raise
    try:
        if hub:
            _select_line(link, hub.switch, "7")
        print(f"Адаптер: {link.version.splitlines()[-1] if link.version else '?'};  "
              f"напряжение: {link.voltage()};  лог обмена: {link.log_path}")
        progress = (lambda t: print(f"\r  {t:<30}", end="", flush=True))
        if args.watch:  # номер замера в бесконечном режиме — только шум в окне
            progress = (lambda t, p=progress: None if t.startswith("живые данные") else p(t))
        on_sample, live_file = None, None
        if args.watch:
            # Бесконечный опрос для окна: каждая строка «LIVE {json}» — один замер; всё пишется в CSV.
            out = Path(args.reports_dir)
            out.mkdir(parents=True, exist_ok=True)
            live_path = out / f"lada_live_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"
            live_file = open(live_path, "w", newline="", encoding="utf-8")
            names = ["t"] + [n for n, *_ in january.PIDS.values()] + ["raw_2101"]
            writer = csv.DictWriter(live_file, fieldnames=names, delimiter=";", extrasaction="ignore")
            writer.writeheader()
            start = time.monotonic()
            print(f"\nЗапись живых данных: {live_path}  (Отмена / Ctrl+C — остановить)")

            def on_sample(sample):
                row = {"t": round(time.monotonic() - start, 2), **sample}
                writer.writerow(row)
                live_file.flush()
                print("LIVE " + json.dumps(row, ensure_ascii=False), flush=True)
        try:
            rep = january.scan(link, -1 if args.watch else args.count, progress,
                               _parse_addr_list(args.addr), on_sample, _print_lada_head)
        except KeyboardInterrupt:
            print("\nОстановлено.")
            return 0
        except DiagError as e:
            print(f"\n{e}.\nПроверьте: зажигание включено, АКБ заряжен (≥ 12 В), K-line не разорвана "
                  "иммобилайзером. Лог обмена: " + str(link.log_path))
            return 2
    finally:
        if live_file:
            live_file.close()
        try:
            if link.mode == "kline":
                link.cmd("ATPC")
            link.close()
        finally:
            if hub:
                hub.close()

    if rep.samples:
        print("\n  Живые данные (мин / среднее / макс):")
        for pid, (name, label, unit, _f) in january.PIDS.items():
            vals = [s[name] for s in rep.samples if name in s]
            if vals:
                print(f"    {label:<38} {min(vals):8.2f} {sum(vals) / len(vals):8.2f} "
                      f"{max(vals):8.2f}  {unit}")
    if rep.samples:
        print("\n  Смесь:")
        for w in january.check_mixture(rep.samples, rep.fuel_system):
            print(f"    • {w}")
    if not args.no_save:
        out = Path(args.reports_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"lada_{datetime.now():%Y-%m-%d_%H-%M-%S}.json"
        path.write_text(json.dumps({"created": datetime.now().isoformat(timespec="seconds"),
                                    "log": str(link.log_path), **rep.to_dict()},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nРезультат: {path}\nЛог обмена: {link.log_path}")
    return 0


def cmd_ecu(args):
    if args.demo:
        print("Команда ecu работает только с реальным адаптером.")
        return 1
    if not args.port and not args.hub:
        print("Укажите порт: --port COM3 (Windows) / /dev/ttyUSB0 (Linux)"
              " и/или --hub COM4 (общий блок obd_hub)")
        return 1
    if args.hub and args.switch:
        print("С --hub переключатель уже в блоке: --switch не нужен.")
        return 1
    lines = [x.strip() for x in args.line.split(",") if x.strip()]
    if len(lines) > 1 and (not (args.switch or args.hub) or args.action not in ("scan-kline", "scan-all")):
        print("Несколько линий (--line 7,8,9,11) можно только для scan-kline/scan-all с --switch.")
        return 1
    from vito_diag.elm import ElmLink, open_serial
    from vito_diag.protocol import hex_bytes, parse_hex_request
    from vito_diag.switch import is_bridge_spec, open_bridge_switch, open_switch

    hub = None
    if args.hub:
        from vito_diag.hub import Hub

        hub = Hub(args.hub, unsafe=args.unsafe)
        print(f"Общий блок: {hub.switch.version}, {hub.switch.state()}; CAN: "
              f"{hub.can.version.splitlines()[-1] if hub.can.version else '?'}")
    bridged = is_bridge_spec(args.switch)
    if hub and not args.port:
        sw, link = hub.switch, hub.can  # только CAN и реле, K-line адаптера нет
    elif bridged:
        # переключатель на HC-06, а к нему ходим через тот же мост ESP32, что и к адаптеру
        ser = open_serial(args.port, baudrate=args.baudrate or 38400, timeout=0.1)
        try:
            sw = open_bridge_switch(args.switch, ser)
            link = ElmLink(args.port, unsafe=args.unsafe, ser=ser)
        except Exception:
            ser.close()
            raise
    else:
        sw = hub.switch if hub else open_switch(args.switch)
        if sw and not hub:
            print(f"Переключатель: {sw.version}, {sw.state()}")
        try:
            link = ElmLink(args.port, baudrate=args.baudrate or 38400, unsafe=args.unsafe)
        except Exception:
            if hub:
                hub.close()
            elif sw:
                sw.close()
            raise
    can_link = hub.can if hub else link
    print(f"Адаптер: {link.version.splitlines()[-1] if link.version else '?'};  "
          f"напряжение: {link.voltage()};  лог обмена: {link.log_path}")
    can_progress = (lambda a: print(f"\r  CAN ID 0x{a:03X}", end="", flush=True))
    kline_progress = (lambda a: print(f"\r  адрес K-line 0x{a:02X}", end="", flush=True))
    modules = []
    try:
        if args.action == "monitor":
            ids = can_link.monitor_can(args.seconds)
            if not ids:
                print("На CAN (контакты 6/14) тишина — это нормально для отдельной диагностической шины.")
            for cid, n in sorted(ids.items()):
                print(f"  0x{cid:03X}: {n} кадров")
            return 0

        if args.action in ("dump", "record"):
            return _live(args, link, can_link, sw, lines[0])

        if args.action in ("raw", "read"):
            args.line = lines[0]
            m = _module_from_args(args)
            if m.bus == "kline":
                _select_line(link, sw, m.line)
            if args.action == "raw":
                payload = (can_link if m.bus == "can" else link).request(m, parse_hex_request(args.request))
                print(hex_bytes(payload))
                return 0
            modules = [m]
            _read_modules(link, modules, can_link, details=args.details)
        if args.action in ("scan-can", "scan-all"):
            print("Поиск блоков на CAN (контакты 6/14). Займёт несколько минут...")
            found = can_link.scan_can(int(args.start, 16), int(args.end, 16), can_progress)
            if can_link.can_header_ignored:
                print("\n  ! Адаптер игнорирует адрес запроса (ATSH) — перебор CAN ID пропущен.\n"
                      "    Видны только блоки, отвечающие на общий OBD-запрос.")
            print(f"\n  найдено на CAN: {len(found)}")
            _read_modules(can_link, found, details=args.details)
            modules += found
        if args.action in ("scan-kline", "scan-all") and not link.has_kline:
            print("Самодельный CAN-адаптер не работает с K-line — поиск на K-line пропущен.")
        elif args.action in ("scan-kline", "scan-all"):
            addrs = _parse_addr_list(args.addrs) if args.addrs else range(0x01, 0xF0)
            for line in lines:
                _select_line(link, sw, line)
                print(f"Поиск блоков на K-line (контакт машины {line}). Это долго — до 10-20 минут...")
                found = link.scan_kline(addrs, line=line, slow="only" if args.slow_only else args.slow,
                                       progress=kline_progress)
                print(f"\n  найдено на K-line (конт. {line}): {len(found)}")
                _read_modules(link, found, details=args.details)
                modules += found
    finally:
        if hub:
            try:
                if link is not hub.can:
                    link.close()
            finally:
                hub.close()
        elif bridged:
            # сначала вернуть линию 7 (порт общий), потом закрыть адаптер
            try:
                if link.mode == "kline":
                    link.cmd("ATPC")
                link.mode = None
                sw.close()
            finally:
                link.close()
        else:
            link.close()
            if sw:
                sw.close()

    if modules and not args.no_save:
        path = _save_modules(modules, args, {"log": str(link.log_path), "kline_pin": ",".join(lines),
                                             "auto_switch": bool(args.switch or args.hub)})
        print(f"\nРезультат: {path}\nЛог обмена: {link.log_path}")
    all_dtcs = [d for m in modules for d in m.dtcs]
    if all_dtcs:
        print_report({"Режим": "опрос блоков (ELM327 напрямую)"}, analyze([], [], extra_dtcs=all_dtcs))
    return 0


def cmd_switch(args):
    from vito_diag.switch import LineSwitch, is_bridge_spec, open_bridge_switch

    if not args.switch:
        print("Укажите порт переключателя: --switch COM7 (или --switch bt --port COM7 через мост ESP32)")
        return 1
    ser = None
    if is_bridge_spec(args.switch):
        if not args.port:
            print("Через мост ESP32 нужен ещё порт моста: --switch bt --port COM7")
            return 1
        from vito_diag.elm import open_serial

        ser = open_serial(args.port, baudrate=38400, timeout=0.1)
        try:
            sw = open_bridge_switch(args.switch, ser, identify=False)  # всё за одно подключение
            sw.require_elm = False  # на столе адаптер может быть не подключён
        except Exception:
            ser.close()
            raise
    else:
        sw = LineSwitch(args.switch)
    restore = True
    try:
        with sw.session():
            print(f"Переключатель: {sw.version}")
            restore = _switch_action(sw, args, bridged=ser is not None)
        if ser is not None:
            restore = False  # через мост Arduino не сбрасывается: линию меняем только явно
    finally:
        sw.close(restore=restore)
        if ser is not None:
            ser.close()
    return 0


def _switch_action(sw, args, bridged=False):
    """Выполнить действие cmd_switch; возвращает, нужно ли потом вернуть линию 7."""
    restore = True
    if args.action == "state":
        print(sw.state())
    elif args.action == "sel":
        sw.select(args.value or "7")
        restore = False
        print(sw.state())
        if not bridged:
            print("Внимание: после отключения от переключателя он сам вернёт линию 7.")
        else:
            print("Линия останется выбранной, пока её не сменить или не отключить питание Arduino.")
    elif args.action == "can":
        sw.can_pair((args.value or "std").lower() == "alt")
        restore = False
        print(sw.state())
    elif args.action == "reset-adapter":
        sw.reset_adapter()
        print("Питание адаптера передёрнуто. ESP32-мост переподключится сам через несколько секунд.")
    elif args.action == "meas":
        for _ in range(max(1, args.count)):
            readings = sw.measure()
            print(f"{'конт.':>5}  {'среднее':>8} {'мин':>6} {'макс':>6}  догадка")
            for pin, r in readings.items():
                print(f"{pin:>5}  {r.avg:>7.2f}В {r.lo:>6.2f} {r.hi:>6.2f}  {r.verdict()}")
            if args.count > 1:
                print()
                time.sleep(1)
    return restore


def cmd_lookup(args):
    from vito_diag.dtc import SEVERITY_RU, lookup

    for code in args.codes:
        d = lookup(code)
        print(f"{d.code}: {d.description}")
        print(f"   Система: {d.system}; серьёзность: {SEVERITY_RU.get(d.severity)}")
        print(f"   Что проверить: {d.advice}\n")
    return 0


def cmd_gui(args):
    from vito_diag.gui import main as gui_main

    return gui_main()


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
    e.add_argument("action", choices=["monitor", "scan-can", "scan-kline", "scan-all", "read", "raw",
                                      "dump", "record"],
                   help="monitor — послушать CAN; scan-* — найти блоки; read — прочитать блок; "
                        "raw — отправить свой запрос; dump — какие LID службы 21 отвечают; "
                        "record — записать выбранные LID во времени (только чтение)")
    e.add_argument("--lids", help="dump/record: LID службы 21, hex (например '01-3F' или '01,05')")
    e.add_argument("request", nargs="?", help="для raw: запрос в hex, например '1A 86'")
    e.add_argument("--can", help="блок на CAN: ID запроса[:ID ответа], hex (например 7E0:7E8)")
    e.add_argument("--kline", help="блок на K-line: адрес, hex (например 10)")
    e.add_argument("--line", default="7",
                   help="контакт машины для K-line (7, 8, 9, 11). С --switch можно список: 7,8,9,11")
    e.add_argument("--switch", help="порт автоматического переключателя линий (Arduino), например COM7; "
                                    "bt — переключатель на HC-06 через тот же мост ESP32, что и адаптер")
    e.add_argument("--hub", help="порт общего блока obd_hub (Arduino: CAN через MCP2515 + реле линий), "
                                 "например COM4; K-line — по-прежнему через --port")
    e.add_argument("--start", default="400", help="scan-can: начальный CAN ID (hex)")
    e.add_argument("--end", default="7FF", help="scan-can: конечный CAN ID (hex)")
    e.add_argument("--addrs", help="scan-kline: адреса, hex (например '01-3F,58')")
    e.add_argument("--slow", action="store_true", help="scan-kline: пробовать и медленную 5-бод инициализацию")
    e.add_argument("--slow-only", action="store_true",
                   help="scan-kline: только медленная 5-бод инициализация (быструю уже проверили)")
    e.add_argument("--details", action="store_true",
                   help="read/scan-*: для каждой ошибки KWP запросить 17 (статус и данные окружения, только чтение)")
    e.add_argument("--seconds", type=float, default=5.0, help="monitor/record: сколько секунд")
    e.add_argument("--unsafe", action="store_true",
                   help="разрешить запросы, меняющие данные в блоках (НЕ использовать без необходимости)")
    e.set_defaults(func=cmd_ecu)

    w = sub.add_parser("switch", help="автоматический переключатель линий: замер контактов, выбор линии")
    w.add_argument("action", choices=["state", "meas", "sel", "can", "reset-adapter"],
                   help="meas — напряжения на контактах; sel 9 — линия; can std|alt — пара CAN; "
                        "reset-adapter — передёрнуть питание ELM327")
    w.add_argument("value", nargs="?", help="для sel: 7/8/9/11; для can: std/alt")
    w.add_argument("--switch", help="порт переключателя (Arduino), например COM7; "
                                    "bt — через мост ESP32 (нужен --port моста)")
    w.add_argument("--port", help="с --switch bt: порт моста ESP32, например COM7")
    w.add_argument("--count", type=int, default=1, help="meas: сколько замеров подряд")
    w.set_defaults(func=cmd_switch)

    j = sub.add_parser("lada", help="ВАЗ «Январь» / Bosch M7.9.7 / М73 / М74 по K-line: "
                                    "опознание, ошибки, живые данные (только чтение)")
    j.add_argument("--port", required=True, help="порт ELM327 / KKL, например COM7")
    j.add_argument("--baudrate", type=int, default=38400, help="скорость порта адаптера")
    j.add_argument("--hub", help="порт obd_hub (реле линий), например COM4 — выберет контакт 7")
    j.add_argument("--addr", default="10,11", help="адреса блока, hex (по умолчанию 10,11)")
    j.add_argument("--count", type=int, default=20, help="сколько опросов живых данных (0 — без них)")
    j.add_argument("--watch", action="store_true",
                   help="живые данные без остановки (для окна): строки LIVE {json} и CSV в reports")
    j.add_argument("--reports-dir", default="reports", help="папка для отчётов")
    j.add_argument("--no-save", action="store_true", help="не сохранять отчёт")
    j.set_defaults(func=cmd_lada)

    g = sub.add_parser("gui", help="окно с кнопками (для тех, кто не любит командную строку)")
    g.set_defaults(func=cmd_gui)

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
