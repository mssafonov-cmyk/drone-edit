"""Island Spirit Yacht Charter (charter-yacht.com, ex-Elite) — NauSYS-виджет оператора (первичный).

POST https://charter-yacht.com/NauSYS/widgets/search-check с полями формы (start/finish dd.mm.yyyy,
filter-location=any, yacht-type=any …) → HTML-выдача «N AVAILABLE YACHTS FOUND» с карточками
«<Имя> <Модель> | Catamaran | <год> Base: … Date: … Price: From X € / Week Y € / Week Length … Berths … Cabins … WC …».
9 запросов. Выдаёт только доступные: лодка флота (ключ islandspirit в ids.json), отсутствующая во
всех 9 окнах, помечается booked — это первичный источник оператора. Цены в $ (Ко Чанг) пропускаются.
"""
import html as _html
import re

from _common import post, load_ids, session, slot, new_boat, pause

NAME = "islandspirit"
URL = "https://charter-yacht.com/NauSYS/widgets/search-check"
CARD = re.compile(r"(?:details|FOUND) ([^|]+?) \| Catamaran \| (\d{4}) Base: (.+?) Date: ([\d.]+) – ([\d.]+) Price: From ([\d,]+) (€|\$) / Week "
                  r"([\d,]+) [€$] / Week Length ([\d.]+) m Berths (\d+) Cabins (\d+) WC (\d+)")


def _plain(h):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", h, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", t)))


def fetch(boats, windows):
    ids = load_ids()
    name2id = {v["islandspirit"].lower(): k for k, v in ids.items() if isinstance(v, dict) and v.get("islandspirit")}
    s = session()
    s.headers["Referer"] = "https://charter-yacht.com/"
    out, new, seen = {}, {}, set()
    for frm, to in windows:
        dd = lambda iso: f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"
        r = post(s, URL, data={"search": "1", "subdir": "", "filter-equipments": "any", "filter-location": "any",
                               "yacht-type": "any", "start": dd(frm), "finish": dd(to), "filter-service": "any",
                               "filter-model": "any", "filter-cabins": "any", "filter-berths": "any",
                               "filter-length": "0-50", "filter-price": "0-100000", "filter-year": "0-20"})
        txt = _plain(r.text)
        if "AVAILABLE YACHTS FOUND" not in txt and "yachts found" not in txt.lower():
            raise RuntimeError("виджет не вернул выдачу")
        for m in CARD.finditer(txt):
            full, year, base, _, _, pfrom, cur, plist, length, berths, cabins, wc = m.groups()
            if "Phuket" not in base or cur != "€":
                continue
            # имя = всё до названия модели; ищем известные имена как префикс
            low = full.lower()
            bid = next((v for k, v in name2id.items() if low.startswith(k + " ")), None)
            price = int(pfrom.replace(",", ""))
            sl = slot(frm, to, price)
            if bid:
                seen.add(bid)
                if bid in boats:
                    u = out.setdefault(bid, {"available_dates": [], "booking_status": "available",
                                             "raw_urls": ["https://charter-yacht.com/phuket/"]})
                    u["available_dates"].append(sl)
            else:
                nm = full.split(" – ")[0]
                nb = new.setdefault(nm, new_boat(model=None, yacht_name=full, year=int(year), base=base,
                                                 operator="Island Spirit Yacht Charter", booking_status="available",
                                                 length_ft=round(float(length) / 0.3048), total_berths_incl_saloon=int(berths),
                                                 toilets=int(wc), urls=["https://charter-yacht.com/phuket/"],
                                                 base_price_note=f"виджет оператора; лист €{int(plist.replace(',', ''))}/нед"))
                nb["available_dates"].append(sl)
        pause(0.5)
    for bid, u in out.items():
        u["base_price_eur"] = min(x["price_eur"] for x in u["available_dates"])
        u["source_note"] = "виджет NauSYS оператора: " + " ".join(f"{x['from'][5:]}:{x['price_eur']}" for x in u["available_dates"])
    for bid in name2id.values():
        if bid in boats and bid not in seen:
            boats[bid]["available_dates"] = []  # первичный источник: чистим устаревшие даты агрегаторов
            out[bid] = {"available_dates": [], "booking_status": "booked",
                        "source_note": "виджет NauSYS оператора: не выдана ни в одном из 9 окон → занята"}
        elif bid in boats and bid in out:
            keep = {(x["from"], x["to"]) for x in out[bid]["available_dates"]}
            boats[bid]["available_dates"] = [x for x in boats[bid].get("available_dates", []) if (x["from"], x["to"]) in keep]
    for nm, nb in new.items():
        nb["base_price_eur"] = min(x["price_eur"] for x in nb["available_dates"])
        out["new:is-" + re.sub(r"[^a-z0-9]+", "-", nm.lower()).strip("-")] = nb
    return out
