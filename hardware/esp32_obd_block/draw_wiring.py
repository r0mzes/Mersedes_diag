"""Монтажная схема блока (как выглядят детали и куда идут провода): wiring.svg.

    python hardware/esp32_obd_block/draw_wiring.py

Детали нарисованы похожими на типовые модули с Алиэкспресса; расположение выводов на вашем
экземпляре сверяйте по надписям на плате. Принципиальная схема — schematic.svg.
"""
from pathlib import Path

OUT = Path(__file__).parent / "wiring.svg"
W, H = 1900, 1290
parts, wires, labels = [], [], []

RED, ORANGE, PURPLE, BLACK = "#d32f2f", "#f57c00", "#8e24aa", "#212121"
BLUE, GREEN, YELLOW, BROWN, WHITE = "#1e63c8", "#2e7d32", "#e0b000", "#8d5524", "#f5f5f5"


def add(s):
    parts.append(s)


def text(x, y, t, size=12, color="#111", anchor="start", weight="400", rot=0):
    tr = f' transform="rotate({rot} {x} {y})"' if rot else ""
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}" '
        f'font-weight="{weight}"{tr}>{t}</text>')


def pin(x, y):
    add(f'<rect x="{x-5}" y="{y-5}" width="10" height="10" fill="#c9a227" stroke="#7a5d00" stroke-width="1"/>'
        f'<circle cx="{x}" cy="{y}" r="2.2" fill="#5b4500"/>')


def wire(points, color, label=None):
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    wires.append(f'<path d="{d}" fill="none" stroke="#000" stroke-opacity=".35" stroke-width="6" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>'
                 f'<path d="{d}" fill="none" stroke="{color}" stroke-width="4" stroke-linejoin="round" '
                 f'stroke-linecap="round"/>')
    for x, y in (points[0], points[-1]):
        wires.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{color}" stroke="#000" stroke-width="1"/>')
    if label:
        (x, y), t = label
        labels.append(f'<rect x="{x-3}" y="{y-12}" width="{len(t)*6.6+6}" height="16" rx="3" fill="#fff" '
                      f'fill-opacity=".9" stroke="{color}"/><text x="{x}" y="{y}" font-size="11" '
                      f'fill="#111">{t}</text>')


def resistor(x, y, bands, vertical=True, label=""):
    """Выводной резистор; возвращает концы выводов."""
    if vertical:
        add(f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y+70}" stroke="#9e9e9e" stroke-width="2.5"/>'
            f'<rect x="{x-8}" y="{y+18}" width="16" height="34" rx="6" fill="#e8d3a8" stroke="#8a7550"/>')
        for i, c in enumerate(bands):
            add(f'<rect x="{x-8}" y="{y+22+i*7}" width="16" height="4" fill="{c}"/>')
        if label:
            text(x + 12, y + 40, label, 11)
        return (x, y), (x, y + 70)
    add(f'<line x1="{x}" y1="{y}" x2="{x+80}" y2="{y}" stroke="#9e9e9e" stroke-width="2.5"/>'
        f'<rect x="{x+20}" y="{y-8}" width="40" height="16" rx="6" fill="#e8d3a8" stroke="#8a7550"/>')
    for i, c in enumerate(bands):
        add(f'<rect x="{x+25+i*8}" y="{y-8}" width="4" height="16" fill="{c}"/>')
    if label:
        text(x + 40, y - 13, label, 11, anchor="middle")
    return (x, y), (x + 80, y)


def ceramic(x, y, label=""):
    add(f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y+44}" stroke="#9e9e9e" stroke-width="2"/>'
        f'<ellipse cx="{x}" cy="{y+20}" rx="10" ry="8" fill="#f0a020" stroke="#8a5a00"/>')
    if label:
        text(x + 13, y + 24, label, 10)
    return (x, y), (x, y + 44)


B100K = ["#6d4c41", "#111", "#f9d71c", "#c9a227"]   # коричн. чёрн. жёлт. — 100 кОм
B22K = ["#d32f2f", "#d32f2f", "#f57c00", "#c9a227"]  # красн. красн. оранж. — 22 кОм
B510 = ["#2e7d32", "#6d4c41", "#6d4c41", "#c9a227"]  # зел. коричн. коричн. — 510 Ом


def perfboard(x, y, w, h, title):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#e9d8a6" stroke="#a88f4f" stroke-width="2"/>')
    dots = "".join(f'<circle cx="{x+10+i*14}" cy="{y+10+j*14}" r="1.6" fill="#b89b55"/>'
                   for i in range(int((w - 14) / 14)) for j in range(int((h - 14) / 14)))
    add(dots)
    text(x + 10, y - 8, title, 13, weight="700")


# ---------------------------------------------------------------- вилка OBD и клеммник
add('<g>')
add('<path d="M40 560 L190 560 L176 650 L54 650 Z" fill="#2b2b2b" stroke="#000" stroke-width="2"/>')
for i in range(8):
    add(f'<rect x="{62+i*14}" y="575" width="8" height="18" fill="#c9a227"/>')
    add(f'<rect x="{66+i*12.5}" y="610" width="8" height="18" fill="#c9a227"/>')
text(115, 545, "Вилка OBD-II (в машину)", 13, anchor="middle", weight="700")
text(115, 670, "или удлинитель папа-мама:", 11, anchor="middle")
text(115, 684, "провода 16, 4, 5, 6, 7, 8, 9, 11, 14", 11, anchor="middle")
add('<path d="M190 600 C215 600 215 520 245 520" fill="none" stroke="#333" stroke-width="14"/>')
add('</g>')

TB = {}
tb_order = ["16", "4", "5", "7", "8", "9", "11", "6", "14"]
tb_name = {"16": "+12 В", "4": "масса", "5": "масса", "7": "K-line", "8": "линия 8", "9": "линия 9",
           "11": "линия 11", "6": "CAN-H", "14": "CAN-L"}
add('<rect x="245" y="335" width="70" height="345" rx="4" fill="#2f8f3a" stroke="#155d20" stroke-width="2"/>')
text(280, 325, "Клеммник KF301", 12, anchor="middle", weight="700")
for i, n in enumerate(tb_order):
    y = 360 + i * 36
    add(f'<rect x="252" y="{y-13}" width="56" height="26" rx="3" fill="#3aa748" stroke="#155d20"/>'
        f'<circle cx="268" cy="{y}" r="8" fill="#cfd8dc" stroke="#607d8b"/>'
        f'<line x1="262" y1="{y}" x2="274" y2="{y}" stroke="#455a64" stroke-width="2"/>')
    text(290, y + 4, n, 12, "#fff", weight="700")
    text(240, y + 4, tb_name[n], 10.5, anchor="end")
    TB[n] = (315, y)
add('<path d="M245 520 L252 520" stroke="#333" stroke-width="14"/>')

# ---------------------------------------------------------------- питание: плата защиты
perfboard(360, 80, 520, 180, "Плата питания и защиты")
# держатель предохранителя
add('<rect x="390" y="110" width="110" height="34" rx="16" fill="#111" stroke="#000"/>'
    '<rect x="415" y="117" width="60" height="20" rx="4" fill="#e0e0e0" stroke="#757575"/>')
text(445, 132, "1 А", 11, anchor="middle", weight="700")
text(445, 165, "F1 предохранитель", 11, anchor="middle")
FUSE_IN, FUSE_OUT = (390, 127), (500, 127)
# TVS SMBJ24A
add('<rect x="540" y="180" width="40" height="26" rx="2" fill="#222"/><rect x="540" y="180" width="8" height="26" fill="#bbb"/>')
text(560, 225, "TVS SMBJ24A", 10.5, anchor="middle")
text(560, 238, "полоса (катод) к +12", 10, anchor="middle")
TVS_K, TVS_A = (540, 193), (580, 193)
# SS34
add('<rect x="590" y="114" width="44" height="26" rx="2" fill="#222"/><rect x="626" y="114" width="8" height="26" fill="#bbb"/>')
text(612, 158, "SS34", 11, anchor="middle")
text(612, 171, "полоса → к VBAT", 10, anchor="middle")
SS_A, SS_K = (590, 127), (634, 127)
# электролит
add('<rect x="700" y="96" width="44" height="62" rx="8" fill="#1c3f94" stroke="#0d235a"/>'
    '<rect x="734" y="96" width="10" height="62" fill="#c5cae9"/>'
    '<text x="722" y="132" font-size="10" fill="#fff" text-anchor="middle">100µ</text>')
text(722, 176, "C1 100 мкФ 50 В", 10.5, anchor="middle")
text(722, 189, "«−» (полоса) на массу", 10, anchor="middle")
C1P, C1N = (712, 158), (732, 158)
ceramic(790, 110, "100 нФ")
C2P, C2N = (790, 110), (790, 154)
text(620, 95, "VBAT_P — после диода", 11, BLUE, anchor="middle")

# ---------------------------------------------------------------- LM2596
add('<rect x="930" y="80" width="230" height="130" rx="6" fill="#1565c0" stroke="#0b3d91" stroke-width="2"/>'
    '<rect x="1010" y="100" width="62" height="62" rx="6" fill="#222"/>'
    '<circle cx="1041" cy="131" r="20" fill="none" stroke="#555" stroke-width="6"/>'
    '<rect x="1090" y="100" width="26" height="26" fill="#2a6fd6" stroke="#fff"/>'
    '<circle cx="1103" cy="113" r="6" fill="#ffd54f"/>'
    '<rect x="955" y="104" width="34" height="46" rx="6" fill="#263238"/>'
    '<rect x="1120" y="140" width="30" height="40" rx="6" fill="#263238"/>')
text(1045, 196, "LM2596: винт → 5,0 В", 12, "#fff", anchor="middle", weight="700")
text(1045, 72, "Понижающий модуль LM2596", 13, anchor="middle", weight="700")
for (x, y, t) in [(940, 115, "IN+"), (940, 175, "IN−"), (1150, 115, "OUT+"), (1150, 175, "OUT−")]:
    pin(x, y)
    text(x + (12 if x < 1000 else -12), y + 4, t, 10.5, "#fff", "start" if x < 1000 else "end")
LM = {"IN+": (940, 115), "IN-": (940, 175), "OUT+": (1150, 115), "OUT-": (1150, 175)}

# ---------------------------------------------------------------- модуль реле
add('<rect x="360" y="330" width="480" height="240" rx="6" fill="#1976d2" stroke="#0b3d91" stroke-width="2"/>')
text(600, 320, "Модуль реле 4 канала 5 В (с оптронами)", 13, anchor="middle", weight="700")
RELAY = {}
for i in range(4):
    x = 380 + i * 112
    add(f'<rect x="{x}" y="350" width="96" height="78" rx="4" fill="#1e88e5" stroke="#0d47a1"/>'
        f'<text x="{x+48}" y="384" font-size="10" fill="#fff" text-anchor="middle">SRD-05VDC-SL-C</text>'
        f'<text x="{x+48}" y="404" font-size="13" fill="#fff" text-anchor="middle" font-weight="700">K{i+1}</text>')
    add(f'<rect x="{x}" y="520" width="96" height="40" rx="3" fill="#2aa24a" stroke="#155d20"/>')
    for j, t in enumerate(["NO", "COM", "NC"]):
        cx = x + 16 + j * 32
        add(f'<circle cx="{cx}" cy="534" r="9" fill="#cfd8dc" stroke="#607d8b"/>'
            f'<line x1="{cx-6}" y1="534" x2="{cx+6}" y2="534" stroke="#455a64" stroke-width="2"/>')
        text(cx, 512, t, 10, "#fff", anchor="middle")
        RELAY[(i + 1, t)] = (cx, 560)
    add(f'<circle cx="{x+80}" cy="445" r="5" fill="#ff5252"/>')
text(600, 590, "K4 не используется", 11, anchor="middle")
hdr = ["GND", "IN1", "IN2", "IN3", "IN4", "VCC"]
for i, t in enumerate(hdr):
    y = 365 + i * 24
    pin(840, y)
    text(828, y + 4, t, 10.5, "#fff", "end")
    RELAY[t] = (840, y)
for i, t in enumerate(["GND", "VCC", "JD-VCC"]):
    y = 395 + 125 + i * 0  # не используется
add('<rect x="760" y="505" width="70" height="20" rx="3" fill="#0d47a1"/>')
text(795, 519, "JD-VCC: снять", 9.5, "#fff", anchor="middle")
pin(850, 548)
text(836, 551, "JD-VCC", 9.5, "#fff", "end")
RELAY["JD-VCC"] = (850, 548)

# ---------------------------------------------------------------- L9637D на переходнике
add('<rect x="975" y="330" width="110" height="140" rx="4" fill="#2e7d32" stroke="#1b5e20" stroke-width="2"/>'
    '<rect x="1005" y="365" width="50" height="70" rx="3" fill="#212121"/>'
    '<circle cx="1013" cy="373" r="3" fill="#555"/>')
text(1030, 405, "L9637D", 10, "#fff", anchor="middle", weight="700")
text(1030, 300, "L9637D на переходнике SO-8 → DIP-8", 12, anchor="middle", weight="700")
U1 = {}
for i, t in enumerate(["1 RX", "2 LO", "3 VCC", "4 TX"]):
    y = 352 + i * 30
    pin(985, y)
    text(997, y + 4, t, 9.5, "#fff")
    U1[t.split()[1]] = (985, y)
for i, t in enumerate(["8 LI", "7 VS", "6 K", "5 GND"]):
    y = 352 + i * 30
    pin(1075, y)
    text(1063, y + 4, t, 9.5, "#fff", "end")
    U1[t.split()[1]] = (1075, y)
text(1030, 488, "2 и 8 — сверить с даташитом", 10, "#b3261e", anchor="middle")

# ---------------------------------------------------------------- преобразователь уровней
add('<rect x="1180" y="330" width="130" height="170" rx="5" fill="#c62828" stroke="#7f0000" stroke-width="2"/>'
    '<rect x="1228" y="350" width="34" height="20" fill="#111"/><rect x="1228" y="390" width="34" height="20" fill="#111"/>'
    '<rect x="1228" y="430" width="34" height="20" fill="#111"/><rect x="1228" y="470" width="34" height="20" fill="#111"/>')
text(1255, 300, "Преобразователь уровней BSS138", 12, anchor="middle", weight="700")
LS = {}
for i, t in enumerate(["HV1", "HV2", "HV", "GND", "HV3", "HV4"]):
    y = 352 + i * 26
    pin(1190, y)
    text(1200, y + 4, t, 9.5, "#fff")
    LS[t] = (1190, y)
for i, t in enumerate(["LV1", "LV2", "LV", "GND2", "LV3", "LV4"]):
    y = 352 + i * 26
    pin(1300, y)
    text(1290, y + 4, t.replace("GND2", "GND"), 9.5, "#fff", "end")
    LS[t] = (1300, y)

# ---------------------------------------------------------------- SN65HVD230
add('<rect x="975" y="640" width="200" height="110" rx="5" fill="#1565c0" stroke="#0b3d91" stroke-width="2"/>'
    '<rect x="1050" y="670" width="40" height="40" fill="#111"/>'
    '<rect x="1110" y="676" width="18" height="10" fill="#555"/>')
text(1075, 630, "Модуль CAN SN65HVD230", 12, anchor="middle", weight="700")
text(1119, 703, "120 Ом:", 9, "#fff", anchor="middle")
text(1119, 714, "выпаять!", 9, "#ffeb3b", anchor="middle", weight="700")
CAN = {}
for i, t in enumerate(["3V3", "GND", "CTX", "CRX"]):
    y = 660 + i * 24
    pin(985, y)
    text(997, y + 4, t, 9.5, "#fff")
    CAN[t] = (985, y)
for i, t in enumerate(["CANH", "CANL"]):
    y = 684 + i * 24
    pin(1165, y)
    text(1153, y + 4, t, 9.5, "#fff", "end")
    CAN[t] = (1165, y)

# ---------------------------------------------------------------- ESP32 DevKit (30 выводов)
EX, EY = 1520, 230
add(f'<rect x="{EX}" y="{EY}" width="200" height="560" rx="8" fill="#1b1b1b" stroke="#000" stroke-width="2"/>'
    f'<rect x="{EX+35}" y="{EY+10}" width="130" height="150" rx="4" fill="#b0b8bf" stroke="#78838c"/>'
    f'<rect x="{EX+35}" y="{EY+10}" width="130" height="34" fill="#2d2d2d"/>'
    f'<path d="M{EX+45} {EY+20} h20 v14 h20 v-14 h20 v14 h20" fill="none" stroke="#c9a227" stroke-width="3"/>'
    f'<text x="{EX+100}" y="{EY+95}" font-size="11" fill="#333" text-anchor="middle" font-weight="700">ESP-WROOM-32</text>'
    f'<rect x="{EX+80}" y="{EY+540}" width="40" height="26" rx="3" fill="#c0c0c0" stroke="#777"/>'
    f'<rect x="{EX+45}" y="{EY+490}" width="22" height="16" rx="3" fill="#ddd"/>'
    f'<rect x="{EX+133}" y="{EY+490}" width="22" height="16" rx="3" fill="#ddd"/>')
text(EX + 56, EY + 520, "EN", 9, "#ddd", anchor="middle")
text(EX + 144, EY + 520, "BOOT", 9, "#ddd", anchor="middle")
text(EX + 100, EY - 10, "ESP32 DevKit V1 (30 выводов)", 13, anchor="middle", weight="700")
text(EX + 100, EY + 585, "micro-USB: прошивка", 10.5, anchor="middle")
left = ["EN", "VP", "VN", "D34", "D35", "D32", "D33", "D25", "D26", "D27", "D14", "D12", "D13", "GND", "VIN"]
right = ["D23", "D22", "TX0", "RX0", "D21", "D19", "D18", "D5", "TX2", "RX2", "D4", "D2", "D15", "GND", "3V3"]
ESP = {}
for i, t in enumerate(left):
    y = EY + 180 + i * 24
    pin(EX + 12, y)
    text(EX + 24, y + 4, t, 10, "#fff")
    ESP["L" + t] = (EX + 12, y)
for i, t in enumerate(right):
    y = EY + 180 + i * 24
    pin(EX + 188, y)
    text(EX + 176, y + 4, t, 10, "#fff", "end")
    ESP["R" + t] = (EX + 188, y)
text(EX + 100, EY + 175, "", 1)

# ---------------------------------------------------------------- шины питания
RY = {"5V": 880, "3V3": 900, "GND": 920}
for name, color in (("5V", ORANGE), ("3V3", PURPLE), ("GND", BLACK)):
    y = RY[name]
    add(f'<rect x="340" y="{y-5}" width="1440" height="10" rx="5" fill="{color}" stroke="#000" stroke-opacity=".4"/>')
text(345, RY["5V"] - 10, "Шины на макетке: +5 В (оранжевая), +3,3 В (фиолетовая), масса (чёрная)", 12, weight="700")
text(1785, RY["5V"] + 4, "+5 В", 11)
text(1785, RY["3V3"] + 4, "+3,3 В", 11)
text(1785, RY["GND"] + 4, "масса", 11)

# ---------------------------------------------------------------- делители
perfboard(360, 990, 900, 270, "Делители замера напряжений (6 шт.): 100 кОм сверху, 22 кОм + 10 нФ снизу")
DIV = {}
for i, (src, gpio) in enumerate([("16", "D32"), ("7", "D33"), ("8", "D34"), ("9", "D35"), ("11", "VP"), ("6", "VN")]):
    x = 420 + i * 140
    (t1, b1) = resistor(x, 1010, B100K, label="100k")
    (t2, b2) = resistor(x, 1110, B22K, label="22k")
    ceramic(x + 45, 1110, "10n")
    add(f'<line x1="{x}" y1="1080" x2="{x}" y2="1110" stroke="#9e9e9e" stroke-width="2.5"/>'
        f'<line x1="{x}" y1="1100" x2="{x+45}" y2="1100" stroke="#9e9e9e" stroke-width="2.5"/>'
        f'<line x1="{x+45}" y1="1100" x2="{x+45}" y2="1110" stroke="#9e9e9e" stroke-width="2.5"/>'
        f'<line x1="{x}" y1="1180" x2="{x+45}" y2="1180" stroke="#9e9e9e" stroke-width="2.5"/>'
        f'<line x1="{x+45}" y1="1154" x2="{x+45}" y2="1180" stroke="#9e9e9e" stroke-width="2.5"/>')
    text(x + 58, 1072, f"конт. {src}", 11, weight="700")
    text(x + 58, 1087, f"→ {gpio}", 11, weight="700")
    DIV[src] = {"top": (x, 1010), "mid": (x - 12, 1095), "gnd": (x + 20, 1180), "gpio": gpio}
    add(f'<line x1="{x-12}" y1="1095" x2="{x}" y2="1095" stroke="#9e9e9e" stroke-width="2.5"/>')

# ================================================================ провода
# OBD 16 -> предохранитель
wire([TB["16"], (335, 360), (335, 127), FUSE_IN], RED, ((330, 300), "+12 В (конт. 16)"))
wire([FUSE_OUT, (520, 127), (520, 193), TVS_K], RED)
wire([(520, 127), SS_A], RED)
wire([TVS_A, (600, 193), (600, 240), (650, 240)], BLACK, ((604, 255), "масса"))
wire([SS_K, (690, 127), (690, 158), C1P], BLUE)
wire([(690, 127), (790, 127), C2P], BLUE)
wire([(790, 127), (900, 127), (900, 115), LM["IN+"]], BLUE, ((812, 120), "VBAT_P"))
# масса платы питания и LM2596 -> шина
wire([C1N, (732, 240), (650, 240), (650, 300), (345, 300), (345, 920)], BLACK)
wire([C2N, (790, 240), (732, 240)], BLACK)
wire([LM["IN-"], (915, 175), (915, 300), (650, 300)], BLACK)
wire([LM["OUT-"], (1185, 175), (1185, 230), (915, 230)], BLACK)
wire([LM["OUT+"], (1340, 115), (1340, 880)], ORANGE, ((1346, 240), "+5 В"))
# масса OBD 4 и 5
wire([TB["4"], (330, 396), (330, 920)], BLACK)
wire([TB["5"], (325, 432), (325, 920)], BLACK)
# VBAT_P -> L9637D VS и 510 Ом
wire([(900, 127), (900, 280), (1110, 280), (1110, 382), U1["VS"]], BLUE)
r_top, r_bot = resistor(1140, 455, B510)
text(1040, 550, "510 Ом 1 Вт →", 10.5, anchor="middle")
wire([(1110, 382), (1140, 382), r_top], BLUE)
wire([r_bot, (1100, 525)], BLUE)
# K-line через реле
wire([TB["7"], (350, 468), (350, 600), RELAY[(1, "NC")][0:1] + (600,), RELAY[(1, "NC")]], BLUE, ((352, 486), "конт. 7"))
wire([TB["8"], (342, 504), (342, 610), (RELAY[(1, "NO")][0], 610), RELAY[(1, "NO")]], "#5c8fe0", ((352, 618), "конт. 8"))
wire([TB["9"], (338, 540), (338, 620), (RELAY[(2, "NC")][0], 620), RELAY[(2, "NC")]], "#3f6fb5", ((470, 628), "конт. 9"))
wire([TB["11"], (334, 576), (334, 630), (RELAY[(2, "NO")][0], 630), RELAY[(2, "NO")]], "#7aa7ef", ((540, 640), "конт. 11"))
wire([RELAY[(1, "COM")], (RELAY[(1, "COM")][0], 650), (RELAY[(3, "NC")][0], 650), RELAY[(3, "NC")]], "#0d3c8c", ((420, 663), "X"))
wire([RELAY[(2, "COM")], (RELAY[(2, "COM")][0], 640), (RELAY[(3, "NO")][0], 640), RELAY[(3, "NO")]], "#0d3c8c", ((600, 655), "Y"))
wire([RELAY[(3, "COM")], (RELAY[(3, "COM")][0], 670), (1100, 670), (1100, 412), U1["K"]], BLUE, ((870, 683), "K_SEL → L9637D K"))
# CAN
wire([TB["6"], (322, 612), (322, 790), (1200, 790), (1200, 684), CAN["CANH"]], GREEN, ((400, 786), "CAN-H (конт. 6)"))
wire([TB["14"], (318, 648), (318, 800), (1210, 800), (1210, 708), CAN["CANL"]], "#66bb6a", ((400, 813), "CAN-L (конт. 14)"))
wire([CAN["CTX"], (955, 708), (955, 820), (1480, 820), (1480, ESP["LD14"][1]), ESP["LD14"]], YELLOW, ((1300, 833), "CTX → D14"))
wire([CAN["CRX"], (945, 732), (945, 840), (1760, 840), (1760, ESP["RD15"][1]), ESP["RD15"]], "#c49000", ((1300, 853), "CRX → D15"))
wire([CAN["3V3"], (965, 660), (965, 900)], PURPLE)
wire([CAN["GND"], (960, 684), (960, 920)], BLACK)
# L9637D питание
wire([U1["VCC"], (960, 412), (960, 600), (1330, 600), (1330, 880)], ORANGE)
wire([U1["GND"], (1088, 442), (1088, 620), (1320, 620), (1320, 920)], BLACK)
# TX/RX через преобразователь уровней
wire([U1["TX"], (968, 442), (968, 318), (1170, 318), (1170, 352), LS["HV1"]], YELLOW)
wire([U1["RX"], (972, 352), (972, 312), (1165, 312), (1165, 378), LS["HV2"]], "#c49000")
wire([LS["HV"], (1160, 404), (1160, 560), (1335, 560), (1335, 880)], ORANGE)
wire([LS["GND"], (1155, 430), (1155, 580), (1325, 580), (1325, 920)], BLACK)
wire([LS["LV"], (1350, 404), (1350, 900)], PURPLE)
wire([LS["GND2"], (1358, 430), (1358, 920)], BLACK)
wire([LS["LV1"], (1420, 352), (1420, 150), (1790, 150), (1790, ESP["RTX2"][1]), ESP["RTX2"]], YELLOW, ((1560, 145), "LV1 → TX2 (GPIO17)"))
wire([LS["LV2"], (1430, 378), (1430, 162), (1780, 162), (1780, ESP["RRX2"][1]), ESP["RRX2"]], "#c49000", ((1560, 180), "LV2 → RX2 (GPIO16)"))
# реле: управление и питание
for n, g in (("IN1", "LD25"), ("IN2", "LD26"), ("IN3", "LD27")):
    x0 = {"IN1": 870, "IN2": 880, "IN3": 890}[n]
    yy = {"IN1": 520, "IN2": 535, "IN3": 550}[n]
    wire([RELAY[n], (x0, RELAY[n][1]), (x0, yy), (1440 + (x0 - 870), yy), (1440 + (x0 - 870), ESP[g][1]), ESP[g]],
         "#e0b000" if n == "IN1" else ("#d4a017" if n == "IN2" else "#b8860b"),
         ((1200, yy + 4), f"{n} → {g[1:]}") if n else None)

wire([RELAY["VCC"], (860, RELAY["VCC"][1]), (860, 900)], PURPLE, ((866, 760), "VCC реле ← 3,3 В"))
wire([RELAY["GND"], (900, RELAY["GND"][1]), (900, 920)], BLACK)
wire([RELAY["JD-VCC"], (850, 870), (850, 880)], ORANGE, ((760, 700), "JD-VCC ← 5 В"))
# ESP32 питание
wire([ESP["LVIN"], (1500, ESP["LVIN"][1]), (1500, 880)], ORANGE, ((1440, 870), "VIN"))
wire([ESP["LGND"], (1490, ESP["LGND"][1]), (1490, 920)], BLACK)
wire([ESP["R3V3"], (1750, ESP["R3V3"][1]), (1750, 900)], PURPLE, ((1690, 870), "3V3"))
# делители
for i, (src, d) in enumerate(DIV.items()):
    wire([TB[src], (TB[src][0] + 8 + i * 2, TB[src][1]), (TB[src][0] + 8 + i * 2, 960 + i * 3),
          (d["top"][0], 960 + i * 3), d["top"]], RED if src == "16" else BROWN)
    wire([d["gnd"], (d["gnd"][0], 1235), (1270, 1235), (1270, 920)], BLACK) if i == 0 else \
        wire([d["gnd"], (d["gnd"][0], 1235)], BLACK)
    gpio = "L" + d["gpio"]
    gx = 1455 + i * 7
    wire([d["mid"], (d["mid"][0], 1250 + i * 5), (gx, 1250 + i * 5), (gx, ESP[gpio][1]), ESP[gpio]], BROWN)
text(1500, 1285, "выходы делителей → VP, VN, D32–D35", 11)

# ---------------------------------------------------------------- сборка
legend = [("+12 В (после диода — синий)", RED), ("+5 В", ORANGE), ("+3,3 В", PURPLE), ("масса", BLACK),
          ("K-line", BLUE), ("CAN", GREEN), ("сигналы ESP32", YELLOW), ("к делителям", BROWN)]
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="Segoe UI, Arial, sans-serif">',
       f'<rect width="{W}" height="{H}" fill="#fafafa"/>',
       '<text x="20" y="32" font-size="22" font-weight="700" fill="#111">Монтажная схема блока vito_diag на ESP32 '
       '(шаг 2) — как соединить детали</text>',
       '<text x="20" y="52" font-size="12.5" fill="#555">Детали нарисованы как типовые модули; надписи выводов '
       'сверяйте по своей плате. Принципиальная схема — schematic.svg. Только чтение.</text>']
for i, (t, c) in enumerate(legend):
    x = 1505 + (i % 2) * 210
    y = 1010 + (i // 2) * 24
    svg.append(f'<rect x="{x}" y="{y-9}" width="34" height="8" rx="4" fill="{c}"/>'
               f'<text x="{x+42}" y="{y}" font-size="12" fill="#111">{t}</text>')
svg += parts + wires + labels + ["</svg>"]
OUT.write_text("\n".join(svg), encoding="utf-8")
print("записан", OUT)
