# RUNBOOK: почасовая проверка катамаранов (Пхукет, 9–24 ноября 2026)

Этот файл — полная инструкция для автономного прогона. Критерии — в `CRITERIA.md`.
Состояние — в `state/`. Ветка: `claude/phuket-catamaran-charter-otvp8p`.

## 0. Подготовка
1. Репозиторий `mssafonov-cmyk/drone-edit` должен быть склонирован, ветка
   `claude/phuket-catamaran-charter-otvp8p` выкачана и актуальна (`git pull`).
2. `TS=$(date -u +%Y-%m-%dT%H%MZ)`. Скопировать текущий `state/snapshot.json` в
   `state/history/$TS-prev.json` ДО любых изменений.
3. Прочитать `CRITERIA.md`, `state/snapshot.json`, последние 20 строк `state/runlog.md`.

## 1. Свежая проверка доступности (НЕ переиспользовать старые результаты)
Проверять ИМЕННО текущую доступность на даты 9–24 ноября 2026, все 7-дневные окна
(9–16 … 17–24), плюс 6–8-дневные, если оператор так предлагает.
Порядок источников:
1. **Charter.boats** — `https://charter.boats/` и `https://api.charter.boats/`. Сайт отдаёт 403
   простым клиентам; пробовать curl с браузерным User-Agent и заголовками Accept/Accept-Language,
   искать публичные эндпоинты поиска (network tab не доступен — смотреть JS-бандл на ссылки
   `/api/`, `search`, `availability`). Если недоступно — зафиксировать и идти дальше.
2. **CharterYacht24** (`https://www.charteryacht24.com/`) — поиск: Thailand / Phuket, catamaran,
   даты по окнам. Это NauSYS-инвентарь (Dream Yacht, Elite, Asia Marine и др.).
3. **YachtReal** (`https://www.yachtreal.com/`) — тот же поиск.
4. **NauSYS публичные страницы** — карточки яхт по `nausys_yacht` id из snapshot
   (`https://www.nausys.com/` и booking-manager виджеты операторов).
5. **Операторы:** Asia Marine (`asia-marine.net`), Dream Yacht Charter
   (`dreamyachtcharter.com`, Thailand), Elite Charters (`charter-yacht.com`), Simpson Yacht
   Charter / Fairview (`simpsonyachtcharter.com`, `fairviewyachting.com`), Sunsail
   (`sunsail.com`, Phuket), The Moorings (`moorings.com`, Thailand), Sweet Dreamers
   (`sweetdreamerscharter.com`), Sail in Asia, Boat Lagoon Yachting.
6. **Агрегаторы для перекрёстной проверки:** 12knots (WebFetch блокируется, curl с UA работает),
   Boataround, 7piers, yacht4less, SamBoat, Boatbookings, Yachting.com, Barbera Yachting.
Правила:
- Индексированная/кэшированная страница ≠ доказательство доступности. Нужен ответ системы
  бронирования с датами (календарь, цена по датам, статус «available/booked/option»).
- Сильный кандидат: подтверждение ≥ 2 источниками ЛИБО первичный источник (оператор/NauSYS).
- Для каждой лодки собирать: base price по датам, mandatory extras (charter pack, cleaning,
  сборы), депозит, watermaker/generator/AC/inverter/electric winch, каюты, berths, салон.
- Валюты переводить в EUR по текущему курсу (указать курс и дату в `base_price_note`).

## 2. Обновить `state/snapshot.json`
Схема на лодку (все поля обязательны, неизвестное = `null`, не выдумывать):
`model, yacht_name, year, length_ft, guest_cabins_double, extra_cabins,
total_berths_incl_saloon, saloon_berth (true/false/null), toilets, base, operator,
operator_rating (0–10 или null), condition_score (0–10 или null), watermaker (true/false/null),
equipment{generator, air_conditioning, electric_winch, inverter, large_fridge,
good_swim_platform, flybridge_or_good_cockpit, solar}, available_dates[{from,to,days,
price_eur,source}], booking_status ("available"|"booked"|"option"|"unknown"),
base_price_eur, base_price_note, mandatory_extras[{name,eur}], mandatory_extras_complete,
deposit_eur, verified (true только при подтверждении по правилу выше), verification_note,
penalties[{reason,points}], source_ids{}, urls[], timestamp, role`.
- ID лодки: стабильный slug по имени (`lola-2`, `karysta`). Не менять существующие ID.
- Лодки, которых больше нет в выдаче, НЕ удалять: `booking_status="unknown"`,
  добавить в `verification_note` дату последнего появления.
- Обновить `generated_at`, `run_id`, `sources_checked` (что реально удалось открыть).

## 3. Детекция событий — только скриптом
```
python3 compare.py state/history/$TS-prev.json state/snapshot.json
```
- exit 3 → событий нет. НЕ уведомлять. Финальное сообщение — одна строка:
  `Без изменений: N лодок проверено, источники: …`.
- exit 0 → есть события. Проверить руками, что событие не артефакт (например, новая лодка
  появилась потому, что раньше источник был недоступен, а не потому, что её выпустили).
  Если событие реальное:
  1. Вызвать `PushNotification` (status=proactive, ≤200 символов, без markdown), например:
     `🔥 NEW Saona 47 Ecstasy 2019 · 12–19 Nov · ~€8,450 total · WM/Gen/AC · score 84 · REQUEST HOLD`
  2. В финальном сообщении — уведомление по шаблону из `CRITERIA.md` (черновик печатает
     `compare.py`), дополненное «Почему интереснее Lola 2 / KARYSTA», «Главный минус»,
     `Recommendation: BOOK / REQUEST HOLD / WATCH / SKIP`. Спорно → WATCH.
- Сеть недоступна / все источники упали → уведомление НЕ слать; записать в runlog.
  Если это третий подряд провальный прогон — отправить одно короткое уведомление о поломке.

## 4. Зафиксировать
1. Дописать строку в `state/runlog.md`: `| $TS | лодок | событий | источники ок/фейл | заметка |`.
2. `git add charter_watch && git commit -m "charter_watch: run $TS — <N> boats, events: <...|none>"`.
3. `git push -u origin claude/phuket-catamaran-charter-otvp8p` (при сетевой ошибке — 4 ретрая
   с паузами 2/4/8/16 с). PR НЕ создавать. Другие файлы репозитория не трогать.

## 5. Ограничения
- Не бронировать, не отправлять запросы операторам, не писать письма от имени пользователя.
- Не менять `CRITERIA.md` без явной просьбы пользователя.
- Не уведомлять «на всякий случай». Ложная срочность хуже пропущенного часа.
