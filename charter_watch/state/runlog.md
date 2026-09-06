# Журнал прогонов

| run (UTC) | лодок | событий | источники | заметка |
|---|---|---|---|---|
| 2026-09-06T0700Z baseline | 15 | 0 | только web-search сниппеты; charter.boats/CY24/YachtReal/NauSYS недоступны из среды Default | базовый snapshot, ничего не подтверждено |
| 2026-09-06T0705Z | 33 | 0 реальных (compare.py: 17 артефактов заполнения baseline — RELEASED/NEW/BETTER_OPTION/PREMIUM_DEAL при пустом baseline) | ok: YachtReal, 12knots API, Boataround API, Island Spirit виджет (NauSYS), Sunsail/Moorings API, BeBlue; partial: CY24 (календари ok, поиск 500), Asia Marine, Fairview, Sweet Dreamers; fail: charter.boats 403, nausys.com, DYC 403, Simpson, yacht4less, yachtic, Barbera | В бюджете с датами: KARYSTA 14–21 ≈€10,5k (4 источника), Maravelle/ex-Ecstasy 14–21 ≈€9,7k, Thai Thunder 15–24 ≈€7,4k (option: CY24 показывает Booked), Lola 2 9–19 option ≈€10,7k, GULL option. Уведомление не отправлено. |
| 2026-09-06T0735Z (maint) | 33 | 0 | — | compare.py: подавление артефактов baseline; добавлены run_check.py, sources/, watchlist.json; интервал → 4 ч; режим экономии |
