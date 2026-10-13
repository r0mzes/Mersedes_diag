# База знаний: диагностика Vito W639 через OBD (ELM327 + vito_diag)

Собрано 2026-10-12 по форумам и справочникам. В каждом файле — ссылки на источники; что выведено нами,
а не прочитано в источнике, помечено «вывод» / «не проверено».

| Файл | О чём |
|---|---|
| [01_raspinovka_obd_mercedes.md](01_raspinovka_obd_mercedes.md) | что на каком контакте OBD у Mercedes, сравнение источников, наш разъём |
| [02_w639_bloki_i_shiny.md](02_w639_bloki_i_shiny.md) | шины и блоки W639, шлюз EZS, почему клон видит только двигатель и АКПП |
| [03_protokoly_i_inicializaciya.md](03_protokoly_i_inicializaciya.md) | K-line, fast/5-бод init, KWP2000 на K-line и CAN, команды ELM327 |
| [04_linii_8_9_11.md](04_linii_8_9_11.md) | разбор опроса 08.10, предупреждение про контакт 8, план замера |
| [05_adaptery_i_plan.md](05_adaptery_i_plan.md) | какие адаптеры и самоделки помогут, рекомендуемый порядок |
| [06_drive2_xentry_adaptery.md](06_drive2_xentry_adaptery.md) | drive2: таблица пинов, какие пины коммутируют адаптеры, DAS/Xentry/HHT/Vediamo, отзывы по моделям |
| [07_drive2_w639_poisk.md](07_drive2_w639_poisk.md) | ~45 постов drive2 по W639: кулиса АКПП на pin 9, EZS с тремя CAN, EGS по CAN в Vediamo, адаптеры, коды |
| [08_github_otkrytye_proekty.md](08_github_otkrytye_proekty.md) | GitHub: протокол Mercedes KWP на CAN, форматы 1A86/1A87, EGS52 (0x7E1/0x7E9), live data 21 30–33, K-line 5-бод |
| [09_forumy_w639.md](09_forumy_w639.md) | форумы 13.10: список блоков W639 (StarCOM), EZS-шлюз, K-line тестер F2, коды P0730/P2135/P2232, ранний W639 без CAN на OBD |
| [10_forumy_chrome.md](10_forumy_chrome.md) | закрытые форумы через Chrome: распиновка EZS W639, тест CAN 60 Ом, CAN-разветвитель, адреса K-line 0x69/0xF3, похожие случаи с кодами |
| [11_opensource_vkladki_13_10.md](11_opensource_vkladki_13_10.md) | вкладки 13.10: odxtools, OBDium, ReDrive, OBDeleven (W639 нет), ecu_diagnostics, Freediag, ISO-TP из ultimate-nag52; идея slcan в obd_hub |

## Главное
1. **Опрос 8/9/11 от 08.10 ничего не доказывает:** клон игнорирует адрес на K-line и всегда будит один
   и тот же блок, поэтому блоки с другими адресами на этих линиях не могли ответить.
2. **Контакт 8 у Mercedes — питание (кл. 15/87), а не K-line** (три независимых источника). До замера
   не подавать его на K-line адаптера: при инициализации адаптер замыкает линию на массу.
3. Контакты 9 и 11 на старых Mercedes — K-линии ABS/ESP и АКПП. На нашей машине АКПП отвечает по CAN,
   так что 11 может быть пустым. В схеме W639 есть отдельный провод от модуля селектора АКПП в OBD.
4. Остальные блоки W639 (ESP, EZS, SAM, приборка…) — на CAN со своими ID, часто через шлюз EZS.
   Клон их не достанет никогда; нужен адаптер с рабочим `ATSH`/flow control или свой CAN на ESP32.

## Коды из наших блоков — что нашлось
- MBClub UK: Vito 111 W639 2009 с P0340 P0611 P2043 **P2135** P2140 одновременно — причиной оказались
  три форсунки (https://forums.mbclub.co.uk/threads/w639-fault-codes-p0340-p0611-p2043-p2135-p2140.276687/).
  Это один случай, не расшифровка кода.
- P2232, P1731, U140B — подтверждённых расшифровок для блоков Mercedes не найдено.

## Что не удалось прочитать
- **drive2.ru закрыт из облака**, но открывается с ПК пользователя через VPN (так собран 06_drive2_xentry_adaptery.md).
- mikrob.ru (клуб микроавтобусов, темы «Can шина…», «Нарушена связь по CAN») — защита от ботов.
- mhhauto.com, forscan.org, benzworld.org — не открылись.
- Глава «CAN bus» и распиновка X11 в схеме W639 — в доступном тексте PDF отсутствуют (вероятно, картинки).

## Подробные отчёты по источникам (sources/)
Собраны на ноутбуке WIN-5BIFGRGGOR7 (папка mersedes-diag\knowledge-base), см. [sources/README.md](sources/README.md):
- [sources/drive2-642803635729735642-star-passthru-pins.md](sources/drive2-642803635729735642-star-passthru-pins.md) — drive2: распиновка OBD, PassThru-адаптеры, DAS/Xentry
- [sources/github-ultimate-nag52-egs52-kwp2000.md](sources/github-ultimate-nag52-egs52-kwp2000.md) — KWP2000 и ISO-TP блока АКПП 722.6 (EGS52/53)
