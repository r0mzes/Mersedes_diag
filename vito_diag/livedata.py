"""Живые данные блока: перебор и запись ответов службы 21 (ReadDataByLocalIdentifier).

Только чтение. Раскладка байтов у Mercedes своя для каждого блока, поэтому сначала
снимаем все отвечающие LID, а потом пишем выбранные во времени и ищем меняющиеся байты.
"""
from __future__ import annotations

import csv
import time
from typing import Callable, Dict, Iterable, List, Optional

from vito_diag.protocol import DiagError, check_positive, hex_bytes


def read_lid(link, module, lid: int) -> List[int]:
    payload = link.request(module, bytes([0x21, lid]))
    check_positive(payload, 0x21)
    return payload


def dump_lids(link, module, lids: Iterable[int],
              progress: Optional[Callable[[int], None]] = None) -> Dict[int, str]:
    """{LID: ответ hex} для всех LID, на которые блок ответил положительно.

    NRC 0x80 (не та сессия) на первом же LID — дальше не перебираем: сессию не меняем.
    """
    found: Dict[int, str] = {}
    for lid in lids:
        if progress:
            progress(lid)
        try:
            payload = read_lid(link, module, lid)
        except DiagError as e:
            if e.nrc == 0x80 and not found:
                raise
            continue
        truncated = module.bus == "can" and getattr(link, "truncated", False)
        found[lid] = hex_bytes(payload) + (" …(обрезано)" if truncated else "")
    return found


def record(link, module, lids: List[int], seconds: float, path, interval: float = 0.0,
           clock: Callable[[], float] = time.monotonic) -> List[dict]:
    """Опрашивает lids по кругу seconds секунд, пишет CSV (t, lid, hex) и возвращает строки."""
    rows: List[dict] = []
    start = clock()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "lid", "data"])
        while clock() - start < seconds:
            for lid in lids:
                try:
                    data = hex_bytes(read_lid(link, module, lid))
                except DiagError as e:
                    data = f"ERR {e}"
                t = round(clock() - start, 3)
                w.writerow([t, f"{lid:02X}", data])
                rows.append({"t": t, "lid": lid, "data": data})
            if interval:
                time.sleep(interval)
    return rows


def changing_bytes(rows: List[dict]) -> List[dict]:
    """Байты, которые менялись за запись: [{lid, index, min, max, values}] (index — от начала ответа)."""
    series: Dict[tuple, List[int]] = {}
    for r in rows:
        if r["data"].startswith("ERR"):
            continue
        for i, b in enumerate(r["data"].split()):
            if not all(c in "0123456789ABCDEF" for c in b):
                break
            series.setdefault((r["lid"], i), []).append(int(b, 16))
    out = []
    for (lid, i), values in sorted(series.items()):
        if len(set(values)) > 1:
            out.append({"lid": lid, "index": i, "min": min(values), "max": max(values),
                        "values": len(set(values))})
    return out
