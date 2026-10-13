# Open-source OBD2: вкладки пользователя (2026-10-13)

Разобраны вкладки, открытые у пользователя в Chrome 13.10 (только чтение). Вывод: **для W639 напрямую
ничего нового** — нет KWP2000-over-CAN под Mercedes, нет адресов блоков W639, нет эмуляции ELM327 на
MCP2515/ESP32. Протокол Mercedes KWP и OpenVehicleDiag уже разобраны в 08_github_otkrytye_proekty.md.

| Проект | Что это | Лицензия / активность | Польза для нас |
|---|---|---|---|
| [mercedes-benz/odxtools](https://github.com/mercedes-benz/odxtools) | Python-библиотека MBition для ODX/PDX (ISO 22901): кодирование/декодирование запросов, CAN ID блоков; CLI `list/browse/snoop/find/decode/compare`; работает через python-can/SocketCAN, не через ELM327. `snoop --active` сам шлёт ISO-TP flow control | MIT, коммит 06.10.2026, 318★ | Условная. Данных Mercedes нет (только пример `somersault.pdx`). Блоки W639 2008 — KWP2000, описания в DAS в CBF/SMR, не ODX. Взять: архитектуру «описание сервисов отдельно от транспорта», логику активного flow control |
| [provrb/OBDium](https://github.com/provrb/obdium) | Rust + Tauri, ELM327 по COM: PID 01, DTC, readiness, VIN по базе NHTSA. Demo mode: запись ответов в `requests.json` и воспроизведение | GPL-3.0, коммит 10.09.2026, 414★ | Низкая. Идея записи/воспроизведения для офлайн-тестов (код не копировать — GPL) |
| [iUnreallx/ReDrive](https://github.com/iUnreallx/ReDrive) | Flutter-приложение (Android/iOS), ELM327 по BT/Wi-Fi/USB, транспорт отделён от разбора. У автора есть эмулятор ELM327 на Qt: https://github.com/iUnreallx/ELM327-Emulator (не открывали) | GPL-3.0, коммит 07.10.2026, 134★ | Низкая. Как тестовый клиент для проверки ELM327-эмуляции obd_hub |
| [Хабр 1033202](https://habr.com/ru/articles/1033202/) | Статья автора ReDrive (08.05.2026) об open-source OBD-приложениях | — | Почти нет; подтверждает «зоопарк» клонов ELM327 |
| [Reddit r/CarHacking nu8uq8](https://www.reddit.com/r/CarHacking/comments/nu8uq8/) | Тред про open-source чтение/запись OBD2; заблокирован расширением, видно только сниппет: советуют CANalyze и **can-utils** | — | can-utils (`isotpsend/isotprecv/isotpdump/cansniffer`) — если obd_hub будет slcan |
| [OBDeleven: Mercedes](https://obdeleven.com/introducing-mercedes-benz) | Коммерческий сканер с лицензией Mercedes (апрель 2026), только устройство OBDeleven 3 | закрытый | **Нет.** Vito в списке только 2015+ (W447), W639 не поддерживается |
| Поиск Google | В выдаче: pyOBD, **Freediag** (K-line/KWP2000 на C), AndrOBD, OpenXC, OpenVehicleDiag, o3DIAG, вопрос на mechanics.stackexchange про сканер на STM32 без ELM (K-line init) | — | Freediag ещё не разбирали — кандидат для K-line на ESP32 |

## Идеи для проекта (вывод, не проверено)
1. **Режим slcan (Lawicel) в obd_hub** в дополнение к эмуляции ELM327. Тогда с адаптером сразу работают
   python-can, can-utils и odxtools, ISO-TP с flow control делает компьютер, а ограничения клона v1.5
   (игнорирует ATSH, нет многокадровых ответов) не мешают.
2. **Запись и воспроизведение обмена в vito_diag** (как Demo mode в OBDium/ReDrive): прогонять парсеры
   на реальных логах без машины. Частично уже есть: `logs/elm_*.log` и `tests/fake_elm.py`.
3. Следующий кандидат на разбор: **Freediag** (K-line, 5-бод/fast init, KWP2000).

## Часть 2: проекты из поисковой выдачи (13.10)
Ultimate NAG52 и OpenVehicleDiag уже разобраны в 08 (CAN ID EGS52, live data, коды). Здесь — только новое.

- **ultimate-nag52-fw, `src/diag/endpoints/endpoint.h` (класс `CanEndpoint`)** — готовая реализация
  ISO-TP на ESP32 (TWAI): SF/FF/CF + flow control, BS/STmin, таймауты. Образец для ISO-TP в obd_hub
  (MCP2515) и esp32_can_adapter. `src/diag/kwp2000.cpp` — сервер KWP2000 по CAN в варианте Mercedes
  (1A, 18, 21, 30, 31, 27, 3E…). GPL-3.0. Тайминги смотреть в 08 (BS 20, STmin 1 мс); в обзоре 13.10
  прозвучало BS 8 / STmin 20 мс — это значения OpenVehicleDiag, не путать.
- **[rnd-ash/ecu_diagnostics](https://github.com/rnd-ash/ecu_diagnostics)** (Rust, GPL-3.0, коммит
  21.08.2026, 235★) — серверы UDS/KWP2000 (v2.2)/OBD2 поверх CAN/ISO-TP/VW-TP2; адаптеры J2534,
  SocketCAN, PCAN и **SLCAN с программным ISO-TP**. Подтверждает идею slcan в obd_hub. Ориентир для
  vito_diag: повтор сессии после ребута блока, ответ 7F xx 78 (response pending).
- **OpenVehicleDiag** — заморожен (релиз 1.0.5 от 05.2021, последний коммит 25.07.2025); SMRParser
  удалён по DMCA, CBFParser остался. С ELM327 не работает.
- **[fenugrec/freediag](https://github.com/fenugrec/freediag)** (C, GPL-3.0, коммит 09.09.2024, 494★) —
  ISO9141 и ISO14230 (KWP2000 на K-line), slow/fast init, тайминги P1–P4, описания интерфейсов в
  `doc_interfaces/`. Образец K-line init для ESP32.
- **Лицензии:** почти всё GPL-3.0 — брать идеи, а не код, иначе vito_diag/obd_hub тоже станут GPL.
  odxtools — MIT.

### Итог по обеим частям
Главный рычаг — **режим slcan в obd_hub**: тогда ecu_diagnostics, python-can + can-isotp, odxtools
и can-utils работают с нашим адаптером, а ISO-TP с flow control делает компьютер. Если остаёмся на
эмуляции ELM327 — ISO-TP писать по образцу `CanEndpoint` из ultimate-nag52-fw.

## Часть 3: детали из ultimate-nag52-fw, которых нет в 08 (13.10)
Остальное (7E1/7E9, сессии, 1A86/1A87, live data 21 30–33) уже в 08.
- **Коды варианта диагностики в эмуляции:** `0x0251` = EGS51, `0x0252` = EGS52, `0x0353` = EGS53
  (src/diag/egs_emulation.cpp). У нашего блока в `1A87` байты 4..5 = **0x0353** → по этой таблице
  **EGS53**, а не EGS52. Вывод, не проверено: так считает эмулятор UN52, по каталогу не сверено.
  Это может объяснить, почему наших 0x1731 и 0xD40B нет в списке кодов EGS52 (08).
- Тайминги (kwp2000_defines.h, can_endpoint.cpp): 4500 мс — таймаут сессии без `3E` (в 08 ошибочно
  назван таймаутом ISO-TP); таймаут ISO-TP — 2000 мс; заполнение кадров 0xCC; FC блока `30 14 01`.
- ISO-TP в can_endpoint.cpp: после FF `1L LL` тестер должен ответить FC `30 BS ST` быстро (таймаут 2 с),
  после каждых BS кадров — новый FC. STmin при передаче у них не соблюдается (код закомментирован);
  в obd_hub на MCP2515 его стоит выдерживать.
- NRC: 10 общий отказ, 11 сервис не поддерживается, 12 формат, 22 условия, 31 диапазон, 33 доступ,
  78 ответ готовится, 80 не та сессия.
- Опасное, не трогать: `31 33 xx` (переключение соленоида ГДТ), `28 01`/`29 01` (отключить/включить
  CAN-рассылку блока) — только в сессии 0x92; по правилам проекта запрещено.
- Коды ошибок в UN52 пустые — формат DTC штатного EGS из прошивки не взять.
