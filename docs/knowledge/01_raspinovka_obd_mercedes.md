# Распиновка 16-контактного OBD у Mercedes и что из этого есть на Vito W639

Mercedes с конца 90-х выводил в 16-контактный разъём **отдельные K-линии на разные группы блоков**
(наследие 38-контактного разъёма под капотом). Стандарт OBD-II оговаривает только 4/5, 6/14, 7, 15, 16 —
остальные контакты «на усмотрение производителя». Источники расходятся, ниже сводка.

## Сводная таблица

| Конт. | pinoutguide / tehcenter.net (общая Mercedes) | Snap-on VCS (Mercedes, 16-pin) | mbclub.co.uk (W163/W209, со слов) | Что у нас на W639 |
|---|---|---|---|---|
| 1 | K-line: EZS, KLA (климат), PTS, SRS | не используется | «ECU» / ключ (EZS) | **нет контакта** |
| 3 | TNA — сигнал оборотов | TNA (бензин) | TNA | нет |
| 4, 5 | масса | масса (кл. 31) | масса | есть |
| 6 / 14 | CAN-H / CAN-L | CAN салона (H/L) | CAN | **есть, отвечает АКПП (0x7E9)** |
| 7 | K-line двигателя (ME/MSM/CDI) | двигатель (ME) | K-line ISO 9141/14230 | **есть, отвечает двигатель 0x12** |
| 8 | «питание (+) зажигание» | **кл. 87 — питание** | **кл. 15, предохранитель 22** | есть — **вероятно, питание, не данные** |
| 9 | K-line ABS/ASR/ESP/ETS/BAS | ETS, только W163 (ML) | ABS/ESP, датчик селектора | есть — неизвестно |
| 11 | K-line АКПП (EGS/ETC/FTC) | ETC (АКПП) | АКПП 722.6 | есть — неизвестно |
| 12 | K-line AAM, радио, ICS | AAM | AAM/EAM, раздатка | нет |
| 13 | K-line SRS (подушки) | SRS | SRS, аудио, Teleaid | нет |
| 15 | K-line приборки (KI, TAU, LWR) | приборка | L-line / приборка | нет |
| 16 | +12 В постоянно | кл. 30 | +12 В | есть |

**Вывод по нашему разъёму (4, 5, 6, 7, 8, 9, 11, 14, 16):**
- Контакт **8 на Mercedes три независимых источника называют питанием (кл. 15 или 87)**, а не K-line.
  Это надо подтвердить замером (см. 04_linii_8_9_11.md), но заранее считать его данными нельзя.
- Контакты 9 и 11 на старых Mercedes были K-линиями ABS/ESP и АКПП. На нашей машине **АКПП уже
  отвечает по CAN** (0x7E1/0x7E9), поэтому K-line АКПП на 11, скорее всего, либо не подключена, либо
  ведёт к другому блоку. Что на 9 — не подтверждено.
- В электросхеме W639 есть разъём **X11/32 «между модулем селектора АКПП (N15/5) и диагностическим
  разъёмом»**, 2 провода: розово-жёлтый (конт. 1) и коричневый (конт. 2, вероятно масса). Значит, хотя
  бы один блок W639 — электронный модуль селектора — заведён в OBD отдельным проводом. На какой
  контакт OBD он приходит — в доступном тексте схемы нет (источник: Technirevue, схема W639).
- Разъём X11 W639 питается через предохранители «F34 f33 Data link connector fuse» и
  «K40/9 f5 Data link connector fuse, exterior lamp switch, instrument cluster» — то есть в разъём
  приходят как минимум два разных питания (кл. 30 на 16 и, вероятно, кл. 15/87 на 8).

## Ссылки
- PinoutGuide, Mercedes OBD-II: https://pinoutguide.com/CarElectronics/mercedes_obd2_diag_pinout.shtml
- tehcenter.net, распиновка разъёмов Mercedes: https://tehcenter.net/raspinovka-diagnosticheskix-razemov-mercedes/
- Snap-on, Mercedes-Benz Vehicle Communication Software Manual (таблица DL-16):
  https://www.snapon.com/DiagnosticsManuals/PDF/NA/VCS/MercedesBenz.pdf
- MBClub UK, «OBD connector pinout» (W209): https://forums.mbclub.co.uk/threads/obd-connector-pinout.13339/
- MBClub UK, «OBDII socket on W163»: https://forums.mbclub.co.uk/threads/obdii-socket-on-w163.69175
- Схема W639 (Technirevue, англ.): http://www.technirevue.com/revue_technique/schema-electrique-mercedes-vito-viano.pdf
- Для сравнения — 14-контактный разъём Sprinter до 2004 (каждый блок на своей K-line):
  https://pinouts.ru/CarElectronics/Mercedes_14_pin_Sprinter_.shtml
