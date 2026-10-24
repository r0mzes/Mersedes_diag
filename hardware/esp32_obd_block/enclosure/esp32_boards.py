# -*- coding: utf-8 -*-
"""Размещение деталей блока на ESP32 на двух макетках (шаг 2,54) и разводка со стороны пайки.

Одно описание на всё: макрос плат (esp32_block_boards.FCMacro) строит по нему 3D и чертежи,
макрос корпуса (esp32_block_case.FCMacro) берёт отсюда модели плат и положение ESP32.

Плата логики «A» — макетка 7x9 см (30 x 24 отверстия), плата питания «B» — 5x7 см (18 x 24).
Координаты отверстий: (c, r) — столбец слева направо и ряд от переднего края (там USB-C ESP32).
Вид — сверху, со стороны деталей. Сторона пайки на чертеже показана зеркально.

Размеры модулей и порядок выводов на них — типовые. Перед пайкой сверить с надписями на своих
платах (особенно преобразователь уровней, SN65HVD230 и переходник SOT-23).

Без FreeCAD (проверка разводки и чертежи):  python3 esp32_boards.py
"""
from collections import deque

PITCH = 2.54
PCB_T = 1.6


class Board:
    def __init__(self, key, title, sx, sy, cols, rows, mount=2.5):
        self.key, self.title = key, title
        self.sx, self.sy, self.cols, self.rows = sx, sy, cols, rows
        self.mx = (sx - (cols - 1) * PITCH) / 2
        self.my = (sy - (rows - 1) * PITCH) / 2
        self.mount = [(mount, mount), (sx - mount, mount), (mount, sy - mount), (sx - mount, sy - mount)]
        self.parts = []      # Part
        self.keepout = set() # отверстия, где нельзя вести провода (зона антенны)
        self.wires = []      # [(net, вид, [(c, r), ...])] — заполняет router
        self.fixed = []      # заданные вручную дорожки: [(net, [(c, r), ...] — точки поворота)]
        self.failed = []     # цепи, которые не развелись

    def xy(self, c, r):
        return self.mx + c * PITCH, self.my + r * PITCH

    def add(self, part):
        self.parts.append(part)
        return part

    def pins(self):
        for p in self.parts:
            for name, (c, r, net) in p.pins.items():
                yield p, name, c, r, net


class Part:
    """kind — тип модели; pins: имя -> (c, r, цепь). net None — вывод не подключать."""

    def __init__(self, ref, kind, title, pins, **kw):
        self.ref, self.kind, self.title, self.pins, self.kw = ref, kind, title, pins, kw


# ------------------------------- цепи -------------------------------
NETS = {  # цепь: (цвет, подпись)
    "GND": ("#212121", "масса"),
    "VBAT": ("#d32f2f", "+12 В после диода (VBAT_P)"),
    "P12": ("#ff5252", "+12 В с конт. 16 (до диода)"),
    "5V": ("#f57c00", "+5 В от LM2596"),
    "3V3": ("#8e24aa", "+3,3 В от ESP32"),
    "K": ("#1e63c8", "K-line L9637D"),
    "CANH": ("#2e7d32", "CAN-H (конт. 6)"),
    "CANL": ("#66bb6a", "CAN-L (конт. 14)"),
    "L7": ("#1565c0", "линия конт. 7"),
    "L8": ("#1976d2", "линия конт. 8"),
    "L9": ("#1e88e5", "линия конт. 9"),
    "L11": ("#42a5f5", "линия конт. 11"),
}
for _n in ("TXD", "RXD", "TXK", "RXK", "CTX", "CRX", "IN1", "IN2", "IN3"):
    NETS[_n] = ("#e0b000", _n)
for _n in ("A16", "A7", "A8", "A9", "A11", "A6"):
    NETS[_n] = ("#8d5524", "замер конт. " + _n[1:])


def net_color(net):
    return NETS.get(net, ("#9e9e9e", net))[0]


# --------------------------- выводы деталей ---------------------------
ESP_LEFT = ["VIN", "GND", "D13", "D12", "D14", "D27", "D26", "D25", "D33", "D32", "D35", "D34",
            "VN", "VP", "EN"]                    # от USB к антенне
ESP_RIGHT = ["3V3", "GND", "D15", "D2", "D4", "RX2", "TX2", "D5", "D18", "D19", "D21", "RX0",
             "TX0", "D22", "D23"]


def esp32(ref, c_left, r0, nets):
    """ESP32 DevKit 30 pin в гнёздах: левый ряд c_left, правый c_left+10, выводы r0..r0+14."""
    pins = {}
    for i, n in enumerate(ESP_LEFT):
        pins["L" + n if n == "GND" else n] = (c_left, r0 + i, nets.get(n))
    for i, n in enumerate(ESP_RIGHT):
        pins["R" + n if n == "GND" else n] = (c_left + 10, r0 + i, nets.get("R" + n if n == "GND" else n))
    pins["LGND"] = (c_left, r0 + 1, nets.get("LGND"))
    return Part(ref, "esp32", "ESP32 DevKit 30 pin", pins, c=c_left, r=r0)


def dip8(ref, title, c_left, r_top, names, nets, chip="", rot180=False):
    """Переходник SOIC-8 -> DIP-8: 1–4 слева сверху вниз, 5–8 справа снизу вверх.
    rot180 — развёрнут: 1–4 справа снизу вверх, 5–8 слева сверху вниз (ключ внизу справа)."""
    pins = {}
    for i in range(4):
        if rot180:
            pins[names[i]] = (c_left + 3, r_top - 3 + i, nets.get(names[i]))
            pins[names[7 - i]] = (c_left, r_top - 3 + i, nets.get(names[7 - i]))
        else:
            pins[names[i]] = (c_left, r_top - i, nets.get(names[i]))
            pins[names[7 - i]] = (c_left + 3, r_top - i, nets.get(names[7 - i]))
    return Part(ref, "dip8", title, pins, c=c_left, r=r_top, chip=chip, rot180=rot180)


def sot23(ref, c_left, r_top, nets, mirror=False, flip=False):
    """Переходник SOT-23 -> 2,54 (13x10, 2x5 выводов); BAT54S: 1 — анод нижнего диода (масса),
    2 — катод верхнего (+3,3 В), 3 — середина (сигнал). Соответствие выводов переходника — сверить."""
    cs, cg = (c_left + 3, c_left) if mirror else (c_left, c_left + 3)
    r1, r2 = (r_top - 2, r_top) if flip else (r_top, r_top - 2)
    pins = {"1": (cs, r1, nets.get("1")), "2": (cs, r2, nets.get("2")),
            "3": (cg, r_top - 1, nets.get("3"))}
    # впаивать только 3 штырька — остальные отверстия под переходником свободны
    return Part(ref, "sot23", "BAT54S на переходнике", pins, c=c_left, r=r_top)


def level8(ref, c_hv, c_lv, r0, nets):
    """Преобразователь уровней 8 кан. (TXS0108E): ряд HV — VB, B1..B8, GND; ряд LV — VA, A1..A8, OE."""
    hv = ["VB"] + ["B%d" % i for i in range(1, 9)] + ["GND"]
    lv = ["VA"] + ["A%d" % i for i in range(1, 9)] + ["OE"]
    pins = {}
    for i in range(10):
        pins[hv[i]] = (c_hv, r0 + i, nets.get(hv[i]))
        pins[lv[i]] = (c_lv, r0 + i, nets.get(lv[i]))
    return Part(ref, "level8", "Преобразователь уровней 8 кан.", pins, c_hv=c_hv, c_lv=c_lv, r=r0)


def sn65(ref, hdr_cells, can_cells, nets, names=("3V3", "GND", "CTX", "CRX")):
    """SN65HVD230 (CJMCU-230): гребёнка 3V3, GND, CTX, CRX и с другого края CANH, CANL.
    Ячейки задаются явно (модуль можно развернуть)."""
    pins = {n: (c, r, nets.get(n)) for (c, r), n in zip(hdr_cells, names)}
    for (c, r), n in zip(can_cells, ("CANH", "CANL")):
        pins[n] = (c, r, nets.get(n))
    return Part(ref, "sn65", "SN65HVD230", pins)


def two(ref, kind, title, p1, p2, n1, n2, **kw):
    return Part(ref, kind, title, {"1": (p1[0], p1[1], n1), "2": (p2[0], p2[1], n2)}, **kw)


def header(ref, title, cells, names, nets):
    """Точки для внешних проводов: провод впаивается прямо в отверстие (разъём не ставим —
    в машине пайка надёжнее, и под ESP32 нет места под гребёнку)."""
    pins = {n: (c, r, nets.get(n, n)) for (c, r), n in zip(cells, names)}
    return Part(ref, "pad", title, pins, order=names)


def kf301(ref, c, r0, names):
    pins = {n: (c, r0 + 2 * i, n2) for i, (n, n2) in enumerate(names)}
    return Part(ref, "kf301", "Клеммник KF301-3P", pins, c=c, r=r0)


# ------------------------------ плата A ------------------------------
ADC_NETS = ["A16", "A7", "A8", "A9", "A11", "A6"]


def board_logic():
    """Слева (к плате B) — SN65HVD230 и L9637D, по центру — преобразователь уровней и разъёмы,
    справа — ESP32 в гнёздах (USB-C к передней стенке). Шесть BAT54S — под ESP32, между рядами
    гнёзд (переходник с тремя штырьками ниже 6 мм, гнёзда 8,5 мм)."""
    b = Board("A", "Плата логики (макетка 7x9 см, 30x24)", 90.0, 70.0, 30, 24)
    # второй GND ESP32 (справа) внутри платы соединён с первым — не подключаем
    b.add(esp32("U1", 19, 2, {"VIN": "5V", "LGND": "GND", "3V3": "3V3",
                              "D14": "CTX", "D15": "CRX", "TX2": "TXD", "RX2": "RXD",
                              "D25": "IN1", "D26": "IN2", "D27": "IN3",
                              "D32": "A16", "D33": "A7", "D34": "A8", "D35": "A9",
                              "VP": "A11", "VN": "A6"}))
    # BAT54S под ESP32: сигнал (3) — к левому ряду гнёзд, масса и +3,3 — к правому
    bat = {(20, 6): "A16", (20, 11): "A7", (20, 16): "A11",
           (24, 6): "A6", (24, 11): "A9", (24, 16): "A8"}
    for k, ((cl, top), net) in enumerate(sorted(bat.items())):
        b.add(sot23("D%d" % (k + 1), cl, top, {"1": "GND", "2": "3V3", "3": net},
                    mirror=True, flip=True))
    b.add(sn65("U2", [(6, 1), (7, 1), (8, 1), (9, 1)], [(7, 9), (8, 9)],
               {"3V3": "3V3", "GND": "GND", "CTX": "CTX", "CRX": "CRX",
                "CANH": "CANH", "CANL": "CANL"}))
    # уровни: канал 1 — приём (B1 <- RX L9637D, A1 -> RX2), канал 4 — передача (A4 <- TX2)
    b.add(level8("U4", 12, 16, 9, {"VB": "5V", "GND": "GND", "B1": "RXK", "B4": "TXK",
                                   "VA": "3V3", "OE": "3V3", "A1": "RXD", "A4": "TXD"}))
    b.add(dip8("U3", "L9637D на переходнике SOIC-8 -> DIP-8", 8, 13,
               ["RX", "LO", "VCC", "TX", "GND", "K", "VS", "LI"],
               {"RX": "RXK", "VCC": "5V", "TX": "TXK", "GND": "GND", "K": "K", "VS": "VBAT"},
               chip="L9637D", rot180=True))
    b.add(two("R1", "res1w", "510 Ом 1 Вт", (1, 15), (6, 15), "VBAT", "K"))
    b.add(two("C1", "cer", "100 нФ (VCC L9637D)", (10, 14), (11, 14), "5V", "GND"))
    b.add(two("C2", "cer", "100 нФ (VS L9637D)", (6, 13), (6, 12), "VBAT", "GND"))
    b.add(header("X1", "питание: +5 В от LM2596, масса, VBAT от платы B", [(0, 23), (1, 23), (2, 23)],
                 ["5V", "GND", "VBAT"], {}))
    b.add(header("X4", "замер от платы B", [(18, r) for r in (10, 11, 12, 13, 14, 15)],
                 ["A7", "A16", "A9", "A8", "A6", "A11"], {}))
    b.add(header("X2", "к модулю реле: IN1–IN3", [(17, 7), (17, 8), (17, 9)],
                 ["IN3", "IN2", "IN1"], {}))
    b.add(header("X10", "к модулю реле: VCC (+3,3 В), GND", [(5, 23), (6, 23)],
                 ["3V3", "GND"], {}))
    b.add(header("X3", "K_SEL к COM реле K3", [(3, 23)], ["K"], {}))
    # зона антенны: за концом гнёзд ESP32 (антенна над рядами 17–21) провода не вести
    b.keepout = {(c, r) for c in range(18, 30) for r in range(17, 24)}
    # под ESP32: +3,3 В — по столбцу 28 от вывода 3V3, масса — по ряду 3 от GND
    b.fixed = [("3V3", [(29, 2), (28, 2), (28, 16), (27, 16)]),
               ("GND", [(19, 3), (27, 3), (27, 4)])]
    return b


# ------------------------------ плата B ------------------------------
# делитель: (выход, вход, ряд) — ряд совпадает с рядом клеммы своего контакта
DIV_ROWS = [("A6", "CANH", 2), ("A7", "L7", 10), ("A8", "L8", 12), ("A9", "L9", 14),
            ("A11", "L11", 16), ("A16", "P12", 18)]


def board_power():
    """Клеммы OBD слева (к гермовводу); каждый делитель в ряду своей клеммы; защита питания
    сзади (к предохранителю и LM2596); выходы к плате A — справа."""
    b = Board("B", "Плата питания и делителей (макетка 5x7 см, 18x24)", 50.0, 70.0, 18, 24)
    b.add(kf301("J1", 1, 2, [("6", "CANH"), ("14", "CANL"), ("4", "GND")]))
    b.add(kf301("J2", 1, 8, [("5", "GND"), ("7", "L7"), ("8", "L8")]))
    b.add(kf301("J3", 1, 14, [("9", "L9"), ("11", "L11"), ("16", "P12")]))
    outs = []
    for i, (out, inp, r) in enumerate(DIV_ROWS):
        b.add(two("R%d" % (2 * i + 1), "res", "100 кОм", (4, r), (8, r), inp, out, bands="100k"))
        b.add(two("R%d" % (2 * i + 2), "res", "22 кОм", (10, r), (14, r), out, "GND", bands="22k"))
        b.add(two("C%d" % (i + 1), "cer", "10 нФ", (11, r + 1), (12, r + 1), out, "GND", small=True))
        outs.append((9, r + 1))
    b.add(two("D1", "sma", "SS34 (полоса — к VBAT)", (3, 20), (6, 20), "P12", "VBAT"))
    b.add(two("D2", "smb", "SMBJ24A (полоса — к +12)", (3, 22), (6, 22), "P12", "GND"))
    b.add(two("C7", "elko", "100 мкФ 50 В", (13, 22), (15, 22), "VBAT", "GND"))
    b.add(two("C8", "cer", "100 нФ", (8, 22), (9, 22), "VBAT", "GND"))
    b.add(header("X6", "CAN к плате A (SN65HVD230)", [(3, 2), (3, 4)], ["CANH", "CANL"], {}))
    b.add(header("X7", "замер к плате A (X4)", outs, [o for o, _, _ in DIV_ROWS], {}))
    b.add(header("X8", "линии к реле: 7, 8, 9, 11", [(3, 10), (3, 12), (3, 14), (3, 16)],
                 ["L7", "L8", "L9", "L11"], {}))
    b.add(header("X9", "питание: VBAT к LM2596 и плате A, масса", [(17, 20), (17, 21), (17, 22)],
                 ["VBAT1", "VBAT2", "GND1"], {"VBAT1": "VBAT", "VBAT2": "VBAT", "GND1": "GND"}))
    return b


# ------------------------------ разводка ------------------------------
def route(b, tries=400, seed=1):
    """Несколько порядков разводки цепей — берём тот, где меньше всего изолированных проводов."""
    import random
    rnd = random.Random(seed)
    best = None
    for t in range(tries):
        route_once(b, rnd if t else None)
        wires = [w for w in b.wires if w[1] == "wire"]
        score = (len(wires), sum(abs(a[0] - c[0]) + abs(a[1] - c[1]) for _, _, (a, c) in wires),
                 sum(len(p) for _, k, p in b.wires if k == "trace"))
        if best is None or score < best[0]:
            best = (score, list(b.wires), list(b.failed))
    b.wires, b.failed = best[1], best[2]
    return b


def route_once(b, rnd=None):
    """Разводка со стороны пайки. Дорожки — голый лужёный провод по отверстиям: разные цепи не
    касаются и не пересекаются. Куски цепи, которые так не соединить, — изолированный провод
    (МГТФ) напрямую между ближайшими выводами; он может пересекать дорожки."""
    owner = {}
    for _, _, c, r, net in b.pins():
        owner[(c, r)] = net if net else "#nc"
    for c, r in b.keepout:
        owner.setdefault((c, r), "#keep")
    for mx, my in b.mount:  # отверстия у крепёжных дырок
        for c in range(b.cols):
            for r in range(b.rows):
                x, y = b.xy(c, r)
                if (x - mx) ** 2 + (y - my) ** 2 < 3.0 ** 2:
                    owner.setdefault((c, r), "#mount")
    nets = {}
    for _, _, c, r, net in b.pins():
        if net:
            nets.setdefault(net, []).append((c, r))

    def span(net):
        pts = nets[net]
        return (max(c for c, _ in pts) - min(c for c, _ in pts)) + \
               (max(r for _, r in pts) - min(r for _, r in pts))
    order = sorted((n for n in nets if len(nets[n]) > 1),
                   key=lambda n: (n in ("GND", "3V3", "5V", "VBAT"), span(n)))
    if rnd:
        rnd.shuffle(order)
        if rnd.random() < 0.5:  # половина попыток — питание первым
            order.sort(key=lambda n: n not in ("GND", "3V3", "5V", "VBAT"))
    b.wires, b.failed = [], []
    fixed_cells = {}
    for net, pts in b.fixed:
        cells = expand(pts)
        for cell in cells:
            owner[cell] = net
        fixed_cells.setdefault(net, []).append(cells)
        b.wires.append((net, "trace", pts))
    for net in order:
        pts = sorted(set(nets[net]))
        groups = []  # куски цепи, соединённые дорожками: (клетки, выводы)
        for cells in fixed_cells.get(net, []):
            groups.append((set(cells), [p for p in pts if p in cells]))
        for p in pts:
            if not any(p in g[0] for g in groups):
                groups.append(({p}, [p]))
        # жадно соединяем дорожками ближайшие куски
        merged = True
        while merged and len(groups) > 1:
            merged = False
            best = None
            for i, (cells, _) in enumerate(groups):
                for j, (cells2, _) in enumerate(groups):
                    if j <= i:
                        continue
                    path = bfs_set(b, owner, net, cells, cells2,
                                   limit=None if best is None else len(best[2]))
                    if path and (best is None or len(path) < len(best[2])):
                        best = (i, j, path)
            if best:
                i, j, path = best
                for cell in path:
                    owner[cell] = net
                b.wires.append((net, "trace", simplify(path)))
                ci, pi = groups[i]
                cj, pj = groups[j]
                groups[i] = (ci | cj | set(path), pi + pj)
                del groups[j]
                merged = True
        # что осталось — изолированным проводом между ближайшими выводами
        while len(groups) > 1:
            best = None
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    for a in groups[i][1]:
                        for c in groups[j][1]:
                            d = (a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2
                            if best is None or d < best[0]:
                                best = (d, i, j, a, c)
            _, i, j, a, c = best
            b.wires.append((net, "wire", [a, c]))
            groups[i] = (groups[i][0] | groups[j][0], groups[i][1] + groups[j][1])
            del groups[j]
    return b


def expand(pts):
    cells = [pts[0]]
    for a, c in zip(pts, pts[1:]):
        dc = (c[0] > a[0]) - (c[0] < a[0])
        dr = (c[1] > a[1]) - (c[1] < a[1])
        cur = a
        while cur != c:
            cur = (cur[0] + dc, cur[1] + dr)
            cells.append(cur)
    return cells


def bfs_set(b, owner, net, sources, targets, limit=None):
    prev = {s: None for s in sources}
    q = deque((s, 0) for s in sources)
    while q:
        cur, dist = q.popleft()
        if limit is not None and dist >= limit:
            return None
        if cur in targets:
            path = [cur]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        c, r = cur
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c + d[0], r + d[1])
            if n in prev or not (0 <= n[0] < b.cols and 0 <= n[1] < b.rows):
                continue
            o = owner.get(n)
            if o is not None and o != net:
                continue
            prev[n] = cur
            q.append((n, dist + 1))
    return None


def bfs(b, owner, net, start, targets):
    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur in targets and cur != start:
            path = [cur]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        c, r = cur
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c + d[0], r + d[1])
            if n in prev or not (0 <= n[0] < b.cols and 0 <= n[1] < b.rows):
                continue
            o = owner.get(n)
            if o is not None and o != net:
                continue
            prev[n] = cur
            q.append(n)
    return None


def simplify(path):
    """Оставить только точки поворота."""
    out = [path[0]]
    for i in range(1, len(path) - 1):
        a, p, n = path[i - 1], path[i], path[i + 1]
        if (p[0] - a[0], p[1] - a[1]) != (n[0] - p[0], n[1] - p[1]):
            out.append(p)
    out.append(path[-1])
    return out


def check(b):
    """Проверки: два вывода в одном отверстии, выводы вне платы, неразведённые цепи."""
    msgs = []
    seen = {}
    for p, name, c, r, net in b.pins():
        if not (0 <= c < b.cols and 0 <= r < b.rows):
            msgs.append("%s.%s вне платы (%d, %d)" % (p.ref, name, c, r))
        if (c, r) in seen:
            msgs.append("%s.%s и %s в одном отверстии (%d, %d)" % (p.ref, name, seen[(c, r)], c, r))
        seen[(c, r)] = "%s.%s" % (p.ref, name)
    for net, rest in b.failed:
        msgs.append("цепь %s не развелась до %s" % (net, rest))
    return msgs


def signature(b):
    import hashlib
    items = sorted((p.ref, n, c, r, net or "") for p, n, c, r, net in b.pins())
    items.append(sorted(b.keepout))
    items.append(b.fixed)
    return hashlib.sha1(repr(items).encode("utf-8")).hexdigest()[:16]


def boards(cache=None):
    """Платы с разводкой. cache — путь к JSON: разводка считается ~20 с, поэтому сохраняется и
    берётся из файла, пока расположение деталей не менялось."""
    import json
    import os
    data = {}
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
    out, changed = [], False
    for b in (board_logic(), board_power()):
        sig = signature(b)
        d = data.get(b.key)
        if d and d.get("sig") == sig:
            b.wires = [(n, k, [tuple(p) for p in pts]) for n, k, pts in d["wires"]]
            b.failed = d.get("failed", [])
        else:
            route(b)
            data[b.key] = {"sig": sig, "wires": b.wires, "failed": b.failed}
            changed = True
        out.append(b)
    if cache and changed:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)
    return out


# Положение ESP32 на плате A (левый нижний угол платы ESP32 от угла макетки) — для корпуса
ESP_W, ESP_L, ESP_ROW = 28.5, 54.0, 25.4
ESP_PIN1 = 9.17          # первый вывод от торца с USB-C


def esp_local():
    b = Board("A", "", 90.0, 70.0, 30, 24)
    x, y = b.xy(19, 2)
    return x - (ESP_W - ESP_ROW) / 2, y - ESP_PIN1


# ------------------------------ чертёж ------------------------------
def draw(b, path, solder_side=False):
    """Вид сверху (детали) или со стороны пайки (зеркально) с сеткой и подписями."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Circle, FancyBboxPatch

    k = 1.0
    fig_w = 4.0 + b.sx / 9.0
    fig, ax = plt.subplots(figsize=(fig_w, 3.2 + b.sy / 9.0))

    def X(x):
        return b.sx - x if solder_side else x

    ax.add_patch(Rectangle((0, 0), b.sx, b.sy, fc="#d9b77a", ec="#6d5320", lw=1.2))
    for mx, my in b.mount:
        ax.add_patch(Circle((X(mx), my), 1.5, fc="white", ec="#6d5320"))
    for c in range(b.cols):
        for r in range(b.rows):
            x, y = b.xy(c, r)
            ax.add_patch(Circle((X(x), y), 0.55, fc="#b08a3e", ec="none"))
    for c in range(b.cols):
        if c % 2 == 0 or b.cols < 20:
            x, _ = b.xy(c, 0)
            ax.text(X(x), -1.8, str(c), ha="center", va="top", fontsize=6, color="#555")
    for r in range(b.rows):
        _, y = b.xy(0, r)
        ax.text(X(-1.2) if not solder_side else X(-1.2), y, str(r), ha="right" if not solder_side else "left",
                va="center", fontsize=6, color="#555")
    if b.keepout:
        cs = [c for c, _ in b.keepout]
        rs = [r for _, r in b.keepout]
        x0, y0 = b.xy(min(cs), min(rs))
        x1, y1 = b.xy(max(cs), max(rs))
        xa, xb = sorted((X(x0 - 1.2), X(x1 + 1.2)))
        ax.add_patch(Rectangle((xa, y0 - 1.2), xb - xa, y1 - y0 + 2.4, fc="#c8f0c8", ec="#2e7d32",
                               ls="--", alpha=0.6))
        ax.text((xa + xb) / 2, (y0 + y1) / 2, "зона антенны:\nне паять провода", ha="center",
                va="center", fontsize=7, color="#2e7d32")
    # провода (сторона пайки): на виде сверху — бледно, на стороне пайки — ярко
    for net, kind, pts in b.wires:
        xs = [X(b.xy(c, r)[0]) for c, r in pts]
        ys = [b.xy(c, r)[1] for c, r in pts]
        a = 1.0 if solder_side else 0.35
        if kind == "wire":  # изолированный провод — с белой окантовкой поверх дорожек
            ax.plot(xs, ys, color="white", lw=4.2 if solder_side else 2.4, alpha=a, zorder=3.5,
                    solid_capstyle="round")
            ax.plot(xs, ys, color=net_color(net), lw=2.2 if solder_side else 1.2, alpha=a,
                    zorder=3.6, ls=(0, (3, 1.2)))
        else:
            ax.plot(xs, ys, color=net_color(net), lw=2.6 if solder_side else 1.4, alpha=a,
                    solid_capstyle="round", zorder=3)
    if not solder_side:
        for p in b.parts:
            outline(ax, b, p, X)
    for p, name, c, r, net in b.pins():
        x, y = b.xy(c, r)
        col = net_color(net) if net else "#ffffff"
        ax.add_patch(Circle((X(x), y), 0.85, fc=col, ec="#000", lw=0.5, zorder=5))
    side = "сторона пайки (вид снизу, зеркально)" if solder_side else "вид сверху, со стороны деталей"
    ax.set_title("%s — %s" % (b.title, side), fontsize=10)
    ax.set_xlim(-5, b.sx + 5)
    ax.set_ylim(-5, b.sy + 3)
    ax.set_aspect("equal")
    ax.axis("off")
    used = []
    for net, _, _ in b.wires:
        if net not in used:
            used.append(net)
    handles = [plt.Line2D([], [], color=net_color(n), lw=3) for n in used]
    ax.legend(handles, [NETS.get(n, ("", n))[1] for n in used], loc="upper left",
              bbox_to_anchor=(1.0, 1.0), fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def bbox_cells(p):
    cs = [v[0] for v in p.pins.values()]
    rs = [v[1] for v in p.pins.values()]
    return min(cs), min(rs), max(cs), max(rs)


def outline(ax, b, p, X):
    from matplotlib.patches import Rectangle, Circle
    c0, r0, c1, r1 = bbox_cells(p)
    x0, y0 = b.xy(c0, r0)
    x1, y1 = b.xy(c1, r1)
    pad = {"esp32": (1.6, 9.2, 1.6, 54.0 - 9.2 - 14 * PITCH), "sn65": (1.6, 1.8, 1.6, 2.4),
           "dip8": (1.6, 1.6, 1.6, 1.6), "sot23": (1.4, 1.2, 1.4, 1.2), "level8": (1.8, 1.6, 1.8, 1.6),
           "kf301": (4.6, 2.6, 2.9, 2.6), "header": (1.27, 1.27, 1.27, 1.27),
           "res": (0.2, 1.2, 0.2, 1.2), "res1w": (0.2, 2.2, 0.2, 2.2), "cer": (1.0, 1.4, 1.0, 1.4),
           "elko": (0, 0, 0, 0), "sma": (0.3, 1.4, 0.3, 1.4), "smb": (0.3, 1.9, 0.3, 1.9)}
    if p.kind == "pad":
        for name, (c, r, net) in p.pins.items():
            x, y = b.xy(c, r)
            ax.add_patch(Circle((X(x), y), 1.45, fc="none", ec="#000", lw=1.0, zorder=6))
            txt = "%s %s" % (p.ref, NETS.get(net, ("", net))[1] if net in ("5V", "GND", "3V3", "VBAT", "K")
                             else (name if not name[-1].isdigit() or name.startswith(("IN", "A")) else net))
            ax.text(X(x) + (1.8 if not (X(1) < X(0)) else -1.8), y + 1.2, txt, fontsize=4.6,
                    ha="left" if not (X(1) < X(0)) else "right", va="bottom", zorder=7,
                    bbox=dict(fc="#fff8c4", ec="none", alpha=0.85, pad=0.3))
        return
    l, d, rr, u = pad.get(p.kind, (1, 1, 1, 1))
    face = {"esp32": "#2b2b2b", "sn65": "#1f5fa8", "dip8": "#2e7d32", "sot23": "#2e7d32",
            "level8": "#b71c1c", "kf301": "#2e9d4a", "header": "#111111", "res": "#e8d3a8",
            "res1w": "#e8d3a8", "cer": "#f0a020", "elko": "#1f3f8f", "sma": "#222", "smb": "#222"}
    if p.kind == "elko":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ax.add_patch(Circle((X(cx), cy), 5.0, fc=face["elko"], ec="#000", alpha=0.55, zorder=4))
    else:
        xa, xb = sorted((X(x0 - l), X(x1 + rr)))
        ax.add_patch(Rectangle((xa, y0 - d), xb - xa, y1 - y0 + d + u, fc=face.get(p.kind, "#777"),
                               ec="#000", lw=0.6, alpha=0.55, zorder=4))
    label = p.ref if p.kind in ("res", "cer", "sma", "smb", "header", "sot23") else "%s %s" % (p.ref, p.title)
    if p.kind == "esp32":
        label = "U1 ESP32 DevKit\n(в гнёздах)"
    if p.kind == "dip8":
        label = "U3 L9637D"
    if p.kind == "level8":
        label = "U4\nуровни\n3,3/5 В"
    if p.kind == "sn65":
        label = "U2 SN65HVD230"
    if p.kind == "kf301":
        label = p.ref
    cx, cy = X((x0 + x1) / 2), (y0 + y1) / 2
    if p.kind == "esp32":
        cy = y0 - 9.2 + 27
    ax.text(cx, cy, label, ha="center", va="center", fontsize=6.5, weight="bold", color="white"
            if p.kind in ("esp32", "sn65", "level8", "dip8", "sot23", "header", "elko", "sma", "smb", "kf301")
            else "#111", zorder=6)
    for name, (c, r, net) in p.pins.items():
        if p.kind in ("esp32", "dip8", "level8", "sn65", "header", "kf301") and net:
            x, y = b.xy(c, r)
            txt = name
            if p.kind in ("header", "kf301"):
                txt = name if p.kind == "kf301" else NETS.get(net, ("", net))[1].split(" ")[0]
                if p.kind == "kf301":
                    txt = "конт. " + name
            right = c == max(v[0] for v in p.pins.values()) and p.kind not in ("kf301",)
            if p.kind == "kf301":
                right = False
            dx = 1.4 if right else -1.4
            ax.text(X(x + dx), y, txt, ha="left" if (right != (X(1) < X(0))) else "right",
                    va="center", fontsize=4.8, color="#000", zorder=7,
                    bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.3))


# ------------------------------ 3D (FreeCAD) ------------------------------
# Плата строится в своих координатах: (0, 0, 0) — левый нижний угол низа макетки, детали сверху,
# дорожки и провода — снизу (сторона пайки).
SPACER = 2.5     # пластиковая планка штыревой гребёнки под модулем


def _rgb(hexcol):
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


C_PERF = (0.80, 0.65, 0.35)
C_BLACK = (0.08, 0.08, 0.08)
C_METAL = (0.75, 0.75, 0.78)
C_GREEN = (0.10, 0.45, 0.20)
C_BLUE = (0.05, 0.35, 0.60)
C_RED = (0.70, 0.12, 0.12)
C_TERM = (0.15, 0.55, 0.25)
C_RES = (0.85, 0.75, 0.55)
C_CAP = (0.95, 0.60, 0.10)


def shapes(b, holes=True, wiring=True):
    """Список (имя, форма, цвет) для платы b в её координатах."""
    import Part
    from FreeCAD import Vector as V

    def box(x, y, z, sx, sy, sz):
        return Part.makeBox(sx, sy, sz, V(x, y, z))

    def cyl(x, y, z, d, h, axis=(0, 0, 1)):
        return Part.makeCylinder(d / 2, h, V(x, y, z), V(*axis))

    t = PCB_T
    out = []
    pcb = box(0, 0, 0, b.sx, b.sy, t)
    for mx, my in b.mount:
        pcb = pcb.cut(cyl(mx, my, -1, 3.0, t + 2))
    out.append(("PCB", pcb, C_PERF))
    if holes:  # отверстия сетки — тёмные кружки на верхней стороне
        dots = [cyl(*b.xy(c, r), t, 1.0, 0.05) for c in range(b.cols) for r in range(b.rows)]
        out.append(("Grid", Part.makeCompound(dots), (0.45, 0.32, 0.12)))
    # выводы: цвет цепи
    by_net = {}
    for p, name, c, r, net in b.pins():
        x, y = b.xy(c, r)
        by_net.setdefault(net or "NC", []).append(cyl(x, y, -0.8, 0.9, t + 2.2))
    for net, lst in by_net.items():
        out.append(("Pins_" + net, Part.makeCompound(lst), _rgb(net_color(net)) if net != "NC"
                    else (1, 1, 1)))
    for p in b.parts:
        for nm, sh, col in part_shapes(p, b, box, cyl):
            out.append((p.ref + "_" + nm, sh, col))
    if wiring:
        for i, (net, kind, pts) in enumerate(b.wires):
            segs = []
            for (c0, r0), (c1, r1) in zip(pts, pts[1:]):
                x0, y0 = b.xy(c0, r0)
                x1, y1 = b.xy(c1, r1)
                if kind == "trace":
                    w = 0.9
                    xa, xb = sorted((x0, x1))
                    ya, yb = sorted((y0, y1))
                    segs.append(box(xa - w / 2, ya - w / 2, -0.7, xb - xa + w, yb - ya + w, 0.7))
                else:
                    L = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                    segs.append(cyl(x0, y0, -2.2, 1.4, L, ((x1 - x0) / L, (y1 - y0) / L, 0)))
                    for x, y in ((x0, y0), (x1, y1)):
                        segs.append(cyl(x, y, -2.2, 1.4, 2.2))
            name = ("Trace_" if kind == "trace" else "Wire_") + net + "_%d" % i
            out.append((name, Part.makeCompound(segs), _rgb(net_color(net))))
    return out


def part_shapes(p, b, box, cyl):
    t = PCB_T
    pins = {n: b.xy(c, r) for n, (c, r, _) in p.pins.items()}
    xs = [v[0] for v in pins.values()]
    ys = [v[1] for v in pins.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    z = t + SPACER
    k = p.kind
    if k == "esp32":
        ex = x0 - (ESP_W - ESP_ROW) / 2
        ey = y0 - ESP_PIN1
        zz = t + 8.5
        res = [("Socket_L", box(x0 - 1.27, y0 - 1.27, t, 2.54, 38.1, 8.5), C_BLACK),
               ("Socket_R", box(x1 - 1.27, y0 - 1.27, t, 2.54, 38.1, 8.5), C_BLACK),
               ("PCB", box(ex, ey, zz, ESP_W, ESP_L, t), (0.12, 0.12, 0.14)),
               ("WROOM", box(ex + (ESP_W - 18) / 2, ey + ESP_L - 25.5, zz + t, 18.0, 18.0, 3.2), C_METAL),
               ("Antenna", box(ex + (ESP_W - 18) / 2, ey + ESP_L - 7.0, zz + t, 18.0, 7.0, 0.8),
                (0.12, 0.12, 0.14)),
               ("USB_C", box(ex + (ESP_W - 9.0) / 2, ey - 1.0, zz + t, 9.0, 7.5, 3.2), C_METAL),
               ("BTN_EN", box(ex + 1.5, ey + 2.5, zz + t, 4.0, 3.0, 1.8), C_METAL),
               ("BTN_BOOT", box(ex + ESP_W - 5.5, ey + 2.5, zz + t, 4.0, 3.0, 1.8), C_METAL),
               ("USB_UART", box(ex + (ESP_W - 5) / 2, ey + 12.0, zz + t, 5.0, 5.0, 1.2), C_BLACK)]
        return res
    if k == "dip8":
        sx, sy = 10.5, 12.7
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return [("Adapter", box(cx - sx / 2, cy - sy / 2, z, sx, sy, t), C_GREEN),
                ("Spacer", box(x0 - 1.27, y0 - 1.27, t, 2.54, y1 - y0 + 2.54, SPACER), C_BLACK),
                ("Spacer2", box(x1 - 1.27, y0 - 1.27, t, 2.54, y1 - y0 + 2.54, SPACER), C_BLACK),
                ("L9637D", box(cx - 2.5, cy - 2.45, z + t, 5.0, 4.9, 1.75), C_BLACK),
                ("Key", cyl(cx + (1.6 if p.kw.get("rot180") else -1.6),
                            cy + (-1.6 if p.kw.get("rot180") else 1.6), z + t + 1.75, 0.8, 0.1),
                 (0.9, 0.9, 0.9))]
    if k == "sot23":
        sx, sy = 10.0, 13.0
        cx = (x0 + x1) / 2
        r_top = max(v[1] for v in p.pins.values())
        cy = b.xy(0, r_top - 2)[1]
        return [("Adapter", box(cx - sx / 2, cy - sy / 2, z, sx, sy, t), C_GREEN),
                ("BAT54S", box(cx - 1.5, cy - 0.7, z + t, 3.0, 1.4, 1.0), C_BLACK)]
    if k == "level8":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        sx, sy = x1 - x0 + 3.4, y1 - y0 + 3.2
        return [("Module", box(cx - sx / 2, cy - sy / 2, z, sx, sy, t), C_RED),
                ("Spacers", box(x0 - 1.27, y0 - 1.27, t, 2.54, y1 - y0 + 2.54, SPACER), C_BLACK),
                ("Spacers2", box(x1 - 1.27, y0 - 1.27, t, 2.54, y1 - y0 + 2.54, SPACER), C_BLACK),
                ("TXS0108E", box(cx - 2.2, cy - 3.25, z + t, 4.4, 6.5, 1.1), C_BLACK)]
    if k == "sn65":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        sx, sy = max(12.0, x1 - x0 + 4.0), y1 - y0 + 3.2
        return [("Module", box(cx - sx / 2, cy - sy / 2, z, sx, sy, t), C_BLUE),
                ("Spacer", box(x0 - 1.27, y0 - 1.27, t, x1 - x0 + 2.54, 2.54, SPACER), C_BLACK),
                ("SN65HVD230", box(cx - 2.5, cy - 2.0, z + t, 5.0, 4.0, 1.75), C_BLACK)]
    if k == "kf301":
        return [("Body", box(x0 - 4.5, y0 - 2.5, t, 7.5, y1 - y0 + 5.0, 10.0), C_TERM)]
    if k in ("res", "res1w"):
        (xa, ya), (xb, yb) = pins["1"], pins["2"]
        L = ((xb - xa) ** 2 + (yb - ya) ** 2) ** 0.5
        d, bl = (4.0, 10.0) if k == "res1w" else (2.4, 6.3)
        ux, uy = (xb - xa) / L, (yb - ya) / L
        zc = t + d / 2 + 0.3
        sx, sy = xa + ux * (L - bl) / 2, ya + uy * (L - bl) / 2
        res = [("Body", cyl(sx, sy, zc, d, bl, (ux, uy, 0)), C_RES),
               ("Lead", cyl(xa, ya, zc, 0.6, L, (ux, uy, 0)), C_METAL)]
        for x, y in ((xa, ya), (xb, yb)):
            res.append(("Leg", cyl(x, y, t, 0.6, zc - t), C_METAL))
        if k == "res" and p.kw.get("bands"):
            bands = {"100k": [(0.43, 0.30, 0.25), (0.07, 0.07, 0.07), (0.98, 0.84, 0.11)],
                     "22k": [(0.83, 0.18, 0.18), (0.83, 0.18, 0.18), (0.96, 0.49, 0.0)]}[p.kw["bands"]]
            for j, col in enumerate(bands):
                s0 = 1.0 + j * 1.0
                res.append(("Band%d" % j, cyl(sx + ux * s0, sy + uy * s0, zc, d + 0.1, 0.5,
                                              (ux, uy, 0)), col))
        return res
    if k == "cer":
        (xa, ya), (xb, yb) = pins["1"], pins["2"]
        cx, cy = (xa + xb) / 2, (ya + yb) / 2
        horiz = abs(xb - xa) > abs(yb - ya)
        sx, sy = (5.0, 2.5) if horiz else (2.5, 5.0)
        if p.kw.get("small"):
            sx, sy = (4.0, 2.2) if horiz else (2.2, 4.0)
        return [("Disc", box(cx - sx / 2, cy - sy / 2, t + 1.5, sx, sy, 4.5), C_CAP)]
    if k == "elko":
        (xa, ya), (xb, yb) = pins["1"], pins["2"]
        return [("Can", cyl((xa + xb) / 2, (ya + yb) / 2, t, 10.0, 20.0), (0.12, 0.22, 0.55)),
                ("Stripe", cyl((xa + xb) / 2, (ya + yb) / 2, t + 20.0, 6.0, 0.2), C_METAL)]
    if k in ("sma", "smb"):
        (xa, ya), (xb, yb) = pins["1"], pins["2"]
        L = abs(xb - xa)
        w = 2.6 if k == "sma" else 3.6
        bl = 4.3 if k == "sma" else 4.6
        cx, cy = (xa + xb) / 2, (ya + yb) / 2
        return [("Body", box(cx - bl / 2, cy - w / 2, t, bl, w, 2.3), C_BLACK),
                ("Stripe", box(cx + bl / 2 - 1.0, cy - w / 2, t + 2.31, 0.6, w, 0.05), C_METAL),
                ("Leads", box(xa, cy - 0.5, t, L, 1.0, 0.3), C_METAL)]
    if k == "pad":  # отрезок провода, уходящий с платы
        res = []
        for n, (x, y) in pins.items():
            net = p.pins[n][2]
            res.append(("Wire_" + n, cyl(x, y, t, 1.6, 6.0), _rgb(net_color(net))))
        return res
    return []


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for bd in boards(os.path.join(here, "esp32_boards_wiring.json")):
        for m in check(bd):
            print("%s: %s" % (bd.key, m))
        print("%s: %d дорожек, %d изолированных проводов" % (bd.key,
              sum(1 for w in bd.wires if w[1] == "trace"), sum(1 for w in bd.wires if w[1] == "wire")))
        name = "board_logic" if bd.key == "A" else "board_power"
        draw(bd, os.path.join(here, name + "_top.png"))
        draw(bd, os.path.join(here, name + "_solder.png"), solder_side=True)
