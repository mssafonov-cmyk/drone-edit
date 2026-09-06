"""Boataround — публичный поисковый API (только ДОСТУПНЫЕ лодки, без экстр/депозита).

GET https://api.boataround.com/v1/search?destinations=phuket&checkIn=…&checkOut=…&category=catamaran
    &currency=EUR&lang=en_EN&limit=50  → data[0].data[] с parameters/price/totalPrice/usp/charter.
9 запросов на прогон. Отсутствие лодки в выдаче ≠ booked (данные Boataround бывают устаревшими),
поэтому booking_status НЕ выставляется — только даты с ценой. Лодки с «Skipper included» — crewed,
пропускаются. Новые лодки (≥4 double, не crewed, нет в ids.json) → new:<slug>.
"""
from _common import get, load_ids, session, slot, new_boat, pause

NAME = "boataround"
API = "https://api.boataround.com/v1/search"


def fetch(boats, windows):
    ids = load_ids()
    slug2id = {v["boataround"]: k for k, v in ids.items() if isinstance(v, dict) and v.get("boataround")}
    s = session()
    out, new = {}, {}
    for frm, to in windows:
        j = get(s, API, params={"destinations": "phuket", "checkIn": frm, "checkOut": to, "category": "catamaran",
                                "currency": "EUR", "lang": "en_EN", "limit": 50}).json()
        for b in j["data"][0]["data"]:
            p = b.get("parameters") or {}
            usp = {u.get("name", "") for u in b.get("usp", [])}
            if any("kipper" in u for u in usp):
                continue
            price = b.get("totalPrice") or b.get("price")
            if not price:
                continue
            sl = slot(frm, to, round(price))
            bid = slug2id.get(b["slug"])
            if bid and bid in boats:
                out.setdefault(bid, {"available_dates": [], "raw_urls": [f"https://www.boataround.com/boat/{b['slug']}"]})
                out[bid]["available_dates"].append(sl)
            elif (p.get("double_cabins") or 0) >= 4 and not bid:
                nb = new.setdefault(b["slug"], new_boat(
                    model=f"{b.get('manufacturer', '')} {b.get('model', '')}".strip(), yacht_name=b.get("title"),
                    year=p.get("year"), length_ft=round(p["length"] / 0.3048) if p.get("length") else None,
                    guest_cabins_double=p.get("double_cabins"), total_berths_incl_saloon=p.get("max_sleeps"),
                    toilets=p.get("toilets"), base=f"{b.get('marina')}, Phuket", operator=b.get("charter"),
                    booking_status="available", source_ids={"boataround": b["slug"]},
                    urls=[f"https://www.boataround.com/boat/{b['slug']}"],
                    base_price_note="Boataround search (после скидки); экстры/депозит не отдаёт"))
                nb["available_dates"].append(sl)
        pause(0.5)
    for bid, u in out.items():
        u["base_price_eur"] = min(x["price_eur"] for x in u["available_dates"])
        u["source_note"] = "Boataround search: " + " ".join(f"{x['from'][5:]}:{x['price_eur']}" for x in u["available_dates"])
    for slug, nb in new.items():
        nb["base_price_eur"] = min(x["price_eur"] for x in nb["available_dates"])
        out["new:" + slug] = nb
    return out
