"""12knots — живой NauSYS/MMK-календарь через Winter CMS AJAX (onChangeDate).

Приём из прогона run1: GET канонической страницы яхты → _token, boatid, baseid, companyid;
затем POST на тот же URL с заголовками X-Requested-With/X-WINTER-REQUEST-HANDLER/X-CSRF-TOKEN
и полями begin/end/boatid/baseid/companyid/pax. Ответ JSON: available (1 = free, 0 = booked,
4 = on hold), origprice/origdiscprice (EUR list / client), origdeposit, extrasContent.

Запросов: 1 + 9 на лодку. Обрабатываются только лодки watchlist с ключом ids.json["12knots"]
(MAX_BOATS ограничивает). Это больше «~15 на источник» из шаблона — иначе календарь не
получить; на прогон ≈ 150 лёгких запросов, ~2 мин.
"""
import re

from _common import get, post, load_ids, priority_ids, session, slot, pause

NAME = "12knots"
MAX_BOATS = 20
STATUS = {1: "available", 0: "booked", 4: "option"}


def _ids(page):
    tok = re.search(r'name="_token" type="hidden" value="([^"]*)"', page)
    f = lambda k: (re.search(rf'id="{k}" value="([^"]*)"', page) or [None, ""])[1]
    return (tok.group(1) if tok else None), f("boatid"), f("baseid"), f("companyid")


def _extras(html_, rate):
    """'Mandatory ... Optional ...' → [{name, eur}] (USD×rate, per day ×7)."""
    if not html_:
        return None
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_))
    m = re.search(r"Mandatory(.*?)(Optional|$)", t)
    if not m:
        return []
    out = []
    body = re.sub(r"^\s*Unit price Total\s*", "", m.group(1))
    for name, amt, unit in re.findall(r"([A-Za-z][A-Za-z &/'\-]+?) \$([\d,]+) \(per (charter|day|week|person)\)", body):
        usd = int(amt.replace(",", ""))
        e = round(usd * rate * (7 if unit == "day" else 1))
        out.append({"name": f"{name.strip()} (12knots ${usd}/{unit})", "eur": e})
    return out


def fetch(boats, windows):
    ids = load_ids()
    s = session()
    out = {}
    for bid in priority_ids(boats)[:MAX_BOATS]:
        url = ids.get(bid, {}).get("12knots")
        if not url:
            continue
        r = get(s, url, allow_redirects=True)
        eff = r.url
        tok, boat, base, comp = _ids(r.text)
        if not tok or not boat:
            out[bid] = {"source_note": "страница без токена/boatid — разбор не удался", "raw_urls": [eff]}
            continue
        hdr = {"X-Requested-With": "XMLHttpRequest", "X-WINTER-REQUEST-HANDLER": "onChangeDate",
               "X-CSRF-TOKEN": tok, "Accept": "application/json", "Referer": eff}
        dates, statuses, notes = [], [], []
        deposit = extras = None
        for frm, to in windows:
            d = post(s, eff, headers=hdr, data={"_token": tok, "begin": frm, "end": to, "boatid": boat,
                                                "baseid": base, "companyid": comp, "pax": "9",
                                                "second_request": "", "offer_id": "", "isMobile": "false"}).json()
            st = STATUS.get(d.get("available"))
            statuses.append(st)
            price = d.get("origdiscprice") or d.get("origprice")
            try:
                price = float(price) if price not in (None, "") else None
            except (TypeError, ValueError):
                price = None
            if d.get("origcurrency") != "EUR":
                price = None
            notes.append(f"{frm[5:]}:{(st or '?')[0]}{'' if not price else ':'+str(price)}")
            if st in ("available", "option") and price:
                dates.append(slot(frm, to, int(price)))
            if st == "available" and extras is None and d.get("extrasContent") is not None:
                try:
                    rate = float(d["origprice"]) / float(d["price"])
                except (TypeError, ValueError, ZeroDivisionError, KeyError):
                    rate = 0.86
                extras = _extras(d["extrasContent"], rate)
            if d.get("deposit_currency") == "EUR" and d.get("origdeposit"):
                try:
                    deposit = int(float(d["origdeposit"]))
                except (TypeError, ValueError):
                    pass
            pause(0.4)
        # NauSYS-ответ авторитетнее агрегаторов: убираем окна, где 12knots говорит booked
        booked_w = {(w[0], w[1]) for w, st in zip(windows, statuses) if st == "booked"}
        boats[bid]["available_dates"] = [x for x in boats[bid].get("available_dates", []) if (x["from"], x["to"]) not in booked_w]
        upd = {"available_dates": dates, "raw_urls": [eff],
               "source_note": "12knots onChangeDate (a=available,o=on hold,b=booked; EUR client): " + " ".join(notes)}
        if all(x == "booked" for x in statuses):
            upd["booking_status"] = "booked"
        elif "available" in statuses:
            upd["booking_status"] = "available"
        elif "option" in statuses:
            upd["booking_status"] = "option"
        if dates:
            upd["base_price_eur"] = min(x["price_eur"] for x in dates)
        if deposit:
            upd["deposit_eur"] = deposit
        if extras is not None:
            upd["mandatory_extras"] = extras
            upd["mandatory_extras_complete"] = True
        out[bid] = upd
    return out
