"""Sunsail / The Moorings (Travelopia) — build-my-quote departures API, Пхукет, ноябрь 2026.

GET https://www.sunsail.com/wp-json/ss-triton/v1/build-my-quote/departures/get?base_id=8022&month=2026-11
GET https://www.moorings.com/wp-json/mr-triton/v1/build-my-quote/departures/get?base_id=6628&month=2026-11
JSON Sunsail: {date: {nights: {"bareboat": {product: {code: {...}}}}}}; Moorings: {date: {nights: {code: {...}}}}.
Цены в USD (visitorCurrency=usd) → EUR по frankfurter.dev (ECB), fallback — константа. 3 запроса.
Коды: 46X4E/46X4K = 454L/4500L (Leopard 45, 4 cab); 47X5*/52X5*/47X4* — 465/5200/4600 → new:.
"""
from _common import get, load_ids, session, slot, new_boat

NAME = "sunsail_moorings"
SRC = {"sunsail": ("https://www.sunsail.com/wp-json/ss-triton/v1/build-my-quote/departures/get", "8022",
                   "https://www.sunsail.com/yacht-charter/thailand/phuket/build-my-quote"),
       "moorings": ("https://www.moorings.com/wp-json/mr-triton/v1/build-my-quote/departures/get", "6628",
                    "https://www.moorings.com/destinations/exotics/thailand-yacht-charters/build-my-quote")}
FALLBACK_RATE = 0.86044  # ECB 2026-09-04


def _rate(s):
    try:
        return float(get(s, "https://api.frankfurter.dev/v1/latest", params={"base": "USD", "symbols": "EUR"}).json()["rates"]["EUR"]), "frankfurter.dev"
    except Exception:  # noqa: BLE001
        return FALLBACK_RATE, "fallback 2026-09-04"


def fetch(boats, windows):
    ids = load_ids()
    s = session()
    rate, rsrc = _rate(s)
    out = {}
    for src, (api, base_id, page) in SRC.items():
        j = get(s, api, params={"base_id": base_id, "month": "2026-11"}).json()
        code2bid = {c: bid for bid, v in ids.items() if isinstance(v, dict) for c in (v.get(src) or [])}
        per_bid = {}
        for frm, to in windows:
            day = j.get(frm, {})
            for nights in ("7",):
                lvl = day.get(nights) or {}
                # Sunsail: nights → "bareboat" → product → code; Moorings: nights → code
                groups = list((lvl.get("bareboat") or {}).values()) if "bareboat" in lvl else [lvl]
                for codes in groups:
                    for code, q in codes.items():
                        if not isinstance(q, dict) or not q.get("price") or q.get("charterType") == "powered":
                            continue
                        bid = code2bid.get(code, "new:" + src + "-" + code.lower())
                        e = round(q["price"] * rate)
                        d = per_bid.setdefault(bid, {"dates": [], "notes": [], "people": q.get("people")})
                        d["dates"].append(slot(frm, to, e))
                        d["notes"].append(f"{frm[5:]}:{code}:${q['price']:.0f}→€{e}")
        for bid, d in per_bid.items():
            # один код на окно: берём минимальную цену, если несколько классов
            best = {}
            for x in d["dates"]:
                k = (x["from"], x["to"])
                if k not in best or x["price_eur"] < best[k]["price_eur"]:
                    best[k] = x
            dates = sorted(best.values(), key=lambda x: x["from"])
            note = f"{src} API 7 ночей, USD→EUR {rate} ({rsrc}): " + " ".join(d["notes"])
            if bid.startswith("new:"):
                code = bid.split("-")[-1].upper()          # напр. 42X3A: 42 ft, X=sail, 3 cabins, A=age class
                cabins = int(code[3]) if len(code) > 3 and code[3].isdigit() else None
                out[bid] = new_boat(model=f"{src} {code}", yacht_name="(флот, без имени)", length_ft=int(code[:2]),
                                    guest_cabins_double=cabins, total_berths_incl_saloon=d.get("people"),
                                    base="Ao Po Grand Marina, Phuket", operator=src, booking_status="available",
                                    available_dates=dates, base_price_eur=min(x["price_eur"] for x in dates),
                                    base_price_note=note, urls=[page])
            elif bid in boats:
                out[bid] = {"available_dates": dates, "booking_status": "available",
                            "base_price_eur": min(x["price_eur"] for x in dates), "source_note": note, "raw_urls": [page]}
    return out
