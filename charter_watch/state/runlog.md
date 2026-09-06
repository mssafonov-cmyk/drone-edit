# Журнал прогонов

| run (UTC) | лодок | событий | источники | заметка |
|---|---|---|---|---|
| 2026-09-06T0700Z baseline | 15 | 0 | только web-search сниппеты; charter.boats/CY24/YachtReal/NauSYS недоступны из среды Default | базовый snapshot, ничего не подтверждено |
| 2026-09-06T0705Z | 33 | 0 реальных (compare.py: 17 артефактов заполнения baseline — RELEASED/NEW/BETTER_OPTION/PREMIUM_DEAL при пустом baseline) | ok: YachtReal, 12knots API, Boataround API, Island Spirit виджет (NauSYS), Sunsail/Moorings API, BeBlue; partial: CY24 (календари ok, поиск 500), Asia Marine, Fairview, Sweet Dreamers; fail: charter.boats 403, nausys.com, DYC 403, Simpson, yacht4less, yachtic, Barbera | В бюджете с датами: KARYSTA 14–21 ≈€10,5k (4 источника), Maravelle/ex-Ecstasy 14–21 ≈€9,7k, Thai Thunder 15–24 ≈€7,4k (option: CY24 показывает Booked), Lola 2 9–19 option ≈€10,7k, GULL option. Уведомление не отправлено. |
| 2026-09-06T0735Z (maint) | 33 | 0 | — | compare.py: подавление артефактов baseline; добавлены run_check.py, sources/, watchlist.json; интервал → 4 ч; режим экономии |
| 2026-09-06T0751Z (сервисный: кодификация фетчеров) | 35 | 1 (PRICE_DROP guiraca — артефакт: окно 9–16 «on hold» €10,684 в run1 не учитывалось; уведомление не слалось) | фетчеры ok: charteryacht24 (16), yachtreal (13), boataround (19), 12knots (15), islandspirit (8), sunsail_moorings (2); ручными остаются: charter.boats (403), DYC (403), Asia Marine (JS-челлендж), Fairview/Sweet Dreamers (без цен), nausys.com (таймаут) | run_check.py ≈ 5 мин; snapshot обновлён только фетчерами |
