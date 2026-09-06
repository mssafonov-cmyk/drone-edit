"""CharterYacht24 — NauSYS-календарь на странице яхты (поиск с датами у сайта отдаёт 500).

GET https://charteryacht24.com/en/yacht/<cy24 из ids.json> → текст «Calendar dd.mm. - dd.mm.yyyy Booked |
dd.mm. - dd.mm.yyyy 9.966 € 11.725 € -15% …». Из периодов 2026 внутри окна 9–24.11 берём цены;
если окно целиком перекрыто блоками Booked и цен нет — booking_status=booked. Блоки могут
перекрываться (опции) — тогда статус не трогаем, только заметка. По одному запросу на лодку
watchlist (≈18), пауза 1.5 с. Экстры/депозит сайт не отдаёт (JS).
"""
import re
from datetime import date

from _common import get, load_ids, priority_ids, session, slot, text, eur, in_window, pause, WINDOW

NAME = "charteryacht24"
BASE = "https://charteryacht24.com/en/yacht/"
PER = re.compile(r"\|(\d{2})\.(\d{2})\. - (\d{2})\.(\d{2})\.(\d{4})\|")


def _calendar(t):
    ci = t.find("|Calendar|")
    if ci < 0:
        return None
    ct = t[ci:t.find("Price Summary", ci) if "Price Summary" in t[ci:] else len(t)]
    ms = list(PER.finditer(ct))
    cal = []
    for i, m in enumerate(ms):
        d1, m1, d2, m2, y2 = m.groups()
        y1 = int(y2) - (1 if int(m1) > int(m2) else 0)
        end = ms[i + 1].start() if i + 1 < len(ms) else len(ct)
        toks = [x.strip() for x in ct[m.end():end].split("|") if x.strip()]
        toks = [x for x in toks if not re.match(r"^(View All Dates|Show Less|View Less Dates)$", x)]
        try:
            a, b = date(y1, int(m1), int(d1)), date(int(y2), int(m2), int(d2))
        except ValueError:
            continue
        cal.append((a, b, toks[:3]))
    return cal


def fetch(boats, windows):
    ids = load_ids()
    s = session()
    out = {}
    for bid in priority_ids(boats):
        path = ids.get(bid, {}).get("cy24")
        if not path:
            continue
        url = BASE + path
        t = text(get(s, url).text)
        cal = _calendar(t)
        if cal is None:
            out[bid] = {"source_note": "страница без блока Calendar", "raw_urls": [url]}
            pause(1.5)
            continue
        dates, booked, notes = [], [], []
        for a, b, toks in cal:
            if b < WINDOW[0] or a > WINDOW[1]:
                continue
            notes.append(f"{a.strftime('%d.%m')}-{b.strftime('%d.%m')}:{' '.join(toks) or '?'}")
            if toks and toks[0] == "Booked":
                booked.append((a, b))
            elif toks and eur(toks[0]) and in_window(a.isoformat(), b.isoformat()):
                dates.append(slot(a.isoformat(), b.isoformat(), eur(toks[0])))
        upd = {"available_dates": dates, "raw_urls": [url], "source_note": "CY24 календарь: " + "; ".join(notes)}
        # окно полностью внутри объединения Booked-блоков и нет цен → booked
        if not dates and booked:
            cover = WINDOW[0]
            for a, b in sorted(booked):
                if a <= cover:
                    cover = max(cover, b)
            overlap = any(b1[1] > b2[0] for b1, b2 in zip(sorted(booked), sorted(booked)[1:]))
            if cover >= WINDOW[1] and not overlap:
                upd["booking_status"] = "booked"
            elif overlap:
                upd["source_note"] += " [перекрывающиеся Booked-блоки — возможно опция]"
        elif dates:
            upd["booking_status"] = "available"
            upd["base_price_eur"] = min(x["price_eur"] for x in dates)
        out[bid] = upd
        pause(1.5)
    return out
