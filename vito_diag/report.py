"""Вывод результатов в консоль и сохранение отчётов (JSON + HTML)."""

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Dict

from vito_diag.analyzer import Analysis
from vito_diag.dtc import SEVERITY_RU

SOURCE_RU = {"stored": "сохранённая", "pending": "ожидающая", "uds": "из блока (UDS)"}


def print_report(info: Dict[str, str], analysis: Analysis) -> None:
    line = "=" * 72
    print(line)
    print(" ДИАГНОСТИКА MERCEDES VITO 4x4")
    print(line)
    for k, v in info.items():
        print(f" {k:<24} {v}")
    print(line)
    print(f" ИТОГ: {analysis.verdict()}")
    print(line)

    if analysis.dtcs:
        for group, items in analysis.by_group().items():
            print(f"\n[{group}]")
            for d in items:
                ecu = f" [{d.ecu}]" if d.ecu else ""
                print(f"  {d.code:<9} {SEVERITY_RU.get(d.severity, d.severity):<10} "
                      f"({SOURCE_RU.get(d.source, d.source)}){ecu}")
                print(f"            {d.description}")
                if d.advice:
                    print(f"            → {d.advice}")

    if analysis.findings:
        print("\n" + line)
        print(" АНАЛИЗ И РЕКОМЕНДАЦИИ")
        print(line)
        for i, f in enumerate(analysis.findings, 1):
            print(f"\n {i}. {f.title}  ({', '.join(f.codes)})")
            print(f"    {f.advice}")

    if analysis.live:
        print("\n" + line)
        print(" ТЕКУЩИЕ ПАРАМЕТРЫ")
        print(line)
        for label, value, unit in analysis.live.values():
            shown = unit if value is None else f"{value:g} {unit}"
            print(f"  {label:<40} {shown}")
    for w in analysis.live_warnings:
        print(f"  ! {w}")
    print()


def save_report(info: Dict[str, str], analysis: Analysis, directory="reports") -> Dict[str, Path]:
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    data = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "vehicle": info,
        "verdict": analysis.verdict(),
        "dtcs": [d.to_dict() for d in analysis.dtcs],
        "findings": [f.__dict__ for f in analysis.findings],
        "live": {k: {"label": l, "value": v, "unit": u} for k, (l, v, u) in analysis.live.items()},
        "live_warnings": analysis.live_warnings,
    }
    json_path = out_dir / f"vito_{stamp}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = out_dir / f"vito_{stamp}.html"
    html_path.write_text(_render_html(data), encoding="utf-8")
    return {"json": json_path, "html": html_path}


def _render_html(data) -> str:
    e = html.escape
    colors = {"critical": "#c62828", "high": "#ef6c00", "medium": "#f9a825", "low": "#2e7d32"}
    rows = "".join(
        f"<tr><td><b>{e(d['code'])}</b></td>"
        f"<td style='color:{colors.get(d['severity'], '#555')}'>{e(SEVERITY_RU.get(d['severity'], d['severity']))}</td>"
        f"<td>{e(d['system'])}</td><td>{e(d['description'])}</td>"
        f"<td>{e(d['advice'])}</td><td>{e(SOURCE_RU.get(d['source'], d['source']))}</td></tr>"
        for d in data["dtcs"]
    ) or "<tr><td colspan=6>Ошибок не найдено</td></tr>"
    findings = "".join(
        f"<li><b>{e(f['title'])}</b> ({e(', '.join(f['codes']))})<br>{e(f['advice'])}</li>"
        for f in data["findings"]
    )
    info = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in data["vehicle"].items())
    live = "".join(
        "<tr><td>{}</td><td>{} {}</td></tr>".format(
            e(v["label"]), "" if v["value"] is None else f"{v['value']:g}", e(v["unit"]))
        for v in data["live"].values()
    )
    warn = "".join(f"<li>{e(w)}</li>" for w in data["live_warnings"])
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Отчёт Vito {e(data['created'])}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:16px;color:#222;background:#fff}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}
td,th{{border:1px solid #ccc;padding:6px;text-align:left;vertical-align:top}}
th{{background:#f3f3f3}} .verdict{{font-size:1.2em;padding:10px;background:#fff3e0}}
</style></head><body>
<h1>Диагностика Mercedes Vito 4x4</h1>
<p>{e(data['created'])}</p>
<table>{info}</table>
<p class="verdict">{e(data['verdict'])}</p>
<h2>Ошибки</h2>
<table><tr><th>Код</th><th>Серьёзность</th><th>Система</th><th>Описание</th><th>Что проверить</th><th>Тип</th></tr>{rows}</table>
{'<h2>Анализ</h2><ol>' + findings + '</ol>' if findings else ''}
{'<h2>Параметры</h2><table>' + live + '</table>' if live else ''}
{'<ul>' + warn + '</ul>' if warn else ''}
</body></html>"""
