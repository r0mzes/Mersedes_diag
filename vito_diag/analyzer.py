"""Анализ набора ошибок: приоритеты, связанные группы, рекомендации."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from vito_diag.dtc import DTC, GROUP_RU, SEVERITY_ORDER, lookup


@dataclass
class Finding:
    title: str
    codes: List[str]
    advice: str
    severity: str = "medium"


@dataclass
class Analysis:
    dtcs: List[DTC]
    findings: List[Finding] = field(default_factory=list)
    live: Dict[str, Tuple[str, Optional[float], str]] = field(default_factory=dict)
    live_warnings: List[str] = field(default_factory=list)

    @property
    def worst_severity(self) -> str:
        if not self.dtcs:
            return "none"
        return min((d.severity for d in self.dtcs), key=lambda s: SEVERITY_ORDER.get(s, 9))

    def by_group(self) -> Dict[str, List[DTC]]:
        groups: Dict[str, List[DTC]] = {}
        for d in self.dtcs:
            groups.setdefault(GROUP_RU.get(d.group, d.group), []).append(d)
        return groups

    def verdict(self) -> str:
        worst = self.worst_severity
        if worst == "none":
            return "Ошибок в доступных по OBD-II блоках не найдено."
        if worst == "critical":
            return "Есть КРИТИЧЕСКИЕ ошибки — эксплуатацию лучше прекратить до проверки."
        if worst == "high":
            return "Есть серьёзные ошибки — нужна диагностика в ближайшее время."
        return "Есть некритичные ошибки — запланируйте проверку."


# Правила-комбинации: если найдены коды из набора — выдаём общую рекомендацию.
PATTERNS = [
    {
        "title": "Проблема с наддувом / впуском воздуха",
        "any": {"P0299", "P2263", "P0234", "P0101", "P0106", "P0045", "P0046"},
        "min": 2,
        "severity": "high",
        "advice": (
            "Сочетание ошибок наддува и расхода воздуха у OM651/OM642 чаще всего даёт "
            "порванный патрубок интеркулера или утечка во впуске. Затем — актуатор/геометрия "
            "турбины и клапан EGR. Проверьте патрубки на масло и трещины, дым-тест впуска."
        ),
    },
    {
        "title": "Сажевый фильтр (DPF) и датчик перепада давления",
        "any": {"P2002", "P2463", "P242F", "P2452", "P2453", "P2454", "P2455",
                "P2458", "P2459", "P0470", "P0471"},
        "min": 1,
        "severity": "high",
        "advice": (
            "Сначала проверьте трубки датчика перепада давления DPF (часто трескаются) и сам датчик. "
            "Если датчик исправен — нужна регенерация (длительная езда по трассе 20-30 минут "
            "или принудительная регенерация сканером). Высокая частота регенераций указывает "
            "на причину сажеобразования: EGR, форсунки, MAF."
        ),
    },
    {
        "title": "EGR и расход воздуха",
        "any": {"P0400", "P0401", "P0402", "P0403", "P0404", "P0405", "P2457", "P2425"},
        "min": 1,
        "severity": "medium",
        "advice": (
            "Клапан EGR на дизеле со временем закоксовывается. Проверьте его ход и каналы, "
            "почистите; также проверьте ДМРВ (MAF)."
        ),
    },
    {
        "title": "Заслонки впускного коллектора (OM646)",
        "any": {"P2004", "P2006", "P2008", "P2009", "P2010", "P2015"},
        "min": 1,
        "severity": "medium",
        "advice": (
            "На OM646 заслонки впускного коллектора закоксовываются, а тяга и пластиковый "
            "шарнир привода ломаются. Снимите привод и проверьте ход заслонок от руки; "
            "при закоксовке — чистка коллектора (обычно вместе с EGR)."
        ),
    },
    {
        "title": "Свечи накала",
        "any": {"P0380", "P0381", "P0670", "P0671", "P0672", "P0673", "P0674"},
        "min": 1,
        "severity": "medium",
        "advice": (
            "Замерьте сопротивление свечей накала (исправная — около 0.5–2 Ом), проверьте "
            "блок управления свечами. Меняйте свечи комплектом; выкручивайте на тёплом "
            "двигателе и аккуратно — они легко обрываются."
        ),
    },
    {
        "title": "Подача топлива",
        "any": {"P0087", "P0088", "P0093", "P0190", "P0191", "P0627"},
        "min": 1,
        "severity": "high",
        "advice": (
            "Начните с топливного фильтра и подсоса воздуха, затем — датчик и регулятор "
            "давления рампы, обратка форсунок (тест пролива), ТНВД."
        ),
    },
    {
        "title": "AdBlue / SCR",
        "any": {"P203F", "P20B9", "P20EE", "P2200", "P2201", "P2BAD"},
        "min": 1,
        "severity": "high",
        "advice": (
            "Проверьте уровень и качество AdBlue, работу дозатора и нагревателя бака. "
            "Не игнорируйте: после обратного отсчёта двигатель может перестать запускаться."
        ),
    },
]


def analyze(stored, pending=(), live=None, extra_dtcs=()) -> Analysis:
    """stored/pending — списки (код, описание) из python-OBD; extra_dtcs — готовые DTC."""
    dtcs: List[DTC] = []
    seen = set()
    for source, items in (("stored", stored), ("pending", pending)):
        for code, desc in items:
            key = (code, source)
            if key in seen:
                continue
            seen.add(key)
            dtcs.append(lookup(code, source=source, ecu_description=desc or ""))
    dtcs.extend(extra_dtcs)
    dtcs.sort(key=lambda d: (SEVERITY_ORDER.get(d.severity, 9), d.code))

    result = Analysis(dtcs=dtcs, live=dict(live or {}))
    codes = {d.code[:5] for d in dtcs}

    for p in PATTERNS:
        hit = sorted(codes & p["any"])
        if len(hit) >= p["min"]:
            result.findings.append(Finding(p["title"], hit, p["advice"], p["severity"]))

    network = sorted(c for c in codes if c.startswith("U"))
    voltage = sorted(codes & {"P0560", "P0562", "P0563"})
    if len(network) >= 2 or (network and voltage):
        result.findings.append(Finding(
            "Много ошибок связи — проверьте питание",
            network + voltage,
            "Несколько ошибок связи одновременно часто вызваны слабым АКБ или плохой массой. "
            "Зарядите/проверьте АКБ (≥12.4 В без нагрузки), клеммы и массы, затем сотрите "
            "ошибки и посмотрите, вернутся ли они.",
            "high",
        ))

    gearbox = sorted(c for c in codes if c[:2] in ("P0", "P1") and c[2:3] in "789")
    if gearbox:
        result.findings.append(Finding(
            "Ошибки коробки передач",
            gearbox,
            "Двигатель лишь сообщает о проблеме в КПП. Подробные коды хранятся в блоке "
            "КПП — найдите и прочитайте его командой `ecu scan-all` (при необходимости через переключатель K-line).",
            "high",
        ))

    if any(d.is_manufacturer_specific for d in dtcs):
        result.findings.append(Finding(
            "Коды производителя Mercedes",
            sorted(d.code for d in dtcs if d.is_manufacturer_specific),
            "Эти коды специфичны для Mercedes. Точную расшифровку даёт Xentry/DAS "
            "или сканер с поддержкой Mercedes (Autel, Launch, iCarsoft MB).",
            "medium",
        ))

    result.live_warnings = check_live(result.live)
    return result


def check_live(live) -> List[str]:
    """Простые проверки живых параметров."""
    warnings = []

    def val(name):
        item = live.get(name)
        return item[1] if item else None

    volt = val("CONTROL_MODULE_VOLTAGE")
    rpm = val("RPM")
    if volt is not None:
        if rpm and rpm > 400 and volt < 13.2:
            warnings.append(f"Напряжение {volt:.1f} В при работающем двигателе — слабая зарядка (норма 13.5–14.7 В).")
        elif volt > 15.0:
            warnings.append(f"Напряжение {volt:.1f} В — перезаряд, проверьте регулятор генератора.")
        elif (not rpm) and volt < 12.2:
            warnings.append(f"Напряжение {volt:.1f} В при заглушенном двигателе — АКБ разряжен.")
    temp = val("COOLANT_TEMP")
    if temp is not None and temp > 105:
        warnings.append(f"Температура ОЖ {temp:.0f} °C — перегрев!")
    if temp is not None and rpm and rpm > 400:
        rt = val("RUN_TIME")
        if rt and rt > 1200 and temp < 70:
            warnings.append(f"Через {rt/60:.0f} мин работы ОЖ всего {temp:.0f} °C — возможно, термостат открыт.")
    return warnings
