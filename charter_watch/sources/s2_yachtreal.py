"""YachtReal — NauSYS white-label, серверный поиск с датами (список только ДОСТУПНЫХ лодок).

GET https://www.yachtreal.com/en/yacht-rental?countries=100174&boat_type=51&date_from=…&date_to=…&page=N
Карточка: <h3 class="title"><a href=…>Модель<br><span>Имя</span></a>, <span class="new-price">9.966 €</span>,
<span class="old-price">…</span>, детали Year/Length/Persons/Cabins/WC, «Charter period».
Rate-limit: пауза 4 с, до 2 страниц на окно (≤ 18 запросов). Отсутствие ≠ booked → booking_status
не выставляется. Для несубботних окон цены DYC-лодок пропорциональные (см. run1) — это заметка,
не подтверждённая цена. Новые Пхукетские катамараны ≥4 кают → new:yr-<slug>.
"""
import re

import requests

from _common import get, load_ids, session, slot, text, eur, new_boat, pause


def _get_retry(s, url, params, tries=4):
    """YachtReal рвёт соединение при частых запросах — ретраи с паузами 6/12/24 с."""
    for i in range(tries):
        try:
            return get(s, url, params=params)
        except (requests.ConnectionError, requests.Timeout):
            if i == tries - 1:
                raise
            pause(6 * 2 ** i)

NAME = "yachtreal"
URL = "https://www.yachtreal.com/en/yacht-rental"


def _cards(h):
    b = h[h.find('id="listing"'):]
    for c in re.split(r'<div class="product-custom boat-item">', b)[1:]:
        m = re.search(r'<h3 class="title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', c, re.S)
        if not m:
            continue
        title = text(m.group(2)).strip("| ").split("|")
        model, name = (title[0].strip(), title[1].strip()) if len(title) > 1 else (title[0].strip(), "")
        price = re.search(r'class="new-price">([^<]+)<', c)
        loc = re.search(r'feather-map-pin"></i>\s*([^<]+)', c)
        det = " ".join(re.findall(r"</i>\s*([^<]+?)\s*</li>", c)[:5])
        yield {"url": m.group(1).split("?")[0], "model": model, "name": name,
               "price": eur(price.group(1)) if price else None,
               "loc": loc.group(1).strip() if loc else "", "det": det}


def fetch(boats, windows):
    ids = load_ids()
    name2id = {v["yachtreal"].lower(): k for k, v in ids.items() if isinstance(v, dict) and v.get("yachtreal")}
    s = session()
    out, new, nreq = {}, {}, 0
    for frm, to in windows:
        for page in (1, 2):
            r = _get_retry(s, URL, {"countries": "100174", "boat_type": "51", "date_from": frm, "date_to": to, "page": page})
            nreq += 1
            cards = list(_cards(r.text))
            for c in cards:
                if "Phuket" not in c["loc"] or not c["price"]:
                    continue
                sl = slot(frm, to, c["price"])
                bid = name2id.get(c["name"].lower())
                if bid and bid in boats:
                    u = out.setdefault(bid, {"available_dates": [], "raw_urls": [c["url"]]})
                    u["available_dates"].append(sl)
                elif not bid:
                    cab = re.search(r"(\d+) Cabins", c["det"])
                    if cab and int(cab.group(1)) >= 4:
                        yr = re.search(r"Year (\d{4})", c["det"]); ln = re.search(r"Length ([\d.]+) m", c["det"])
                        per = re.search(r"(\d+) Persons", c["det"])
                        slug = c["url"].rstrip("/").split("/yacht/")[-1].replace("/", "-")
                        nb = new.setdefault(slug, new_boat(
                            model=c["model"], yacht_name=c["name"], year=int(yr.group(1)) if yr else None,
                            length_ft=round(float(ln.group(1)) / 0.3048) if ln else None,
                            total_berths_incl_saloon=int(per.group(1)) if per else None, base=c["loc"],
                            booking_status="available", urls=[c["url"]], source_ids={"yachtreal": slug},
                            base_price_note="YachtReal список (цена сайта); экстры/депозит не отдаёт"))
                        nb["available_dates"].append(sl)
            pause(5)
            if len(cards) < 20:
                break
    for bid, u in out.items():
        u["base_price_eur"] = min(x["price_eur"] for x in u["available_dates"])
        u["source_note"] = "YachtReal поиск: " + " ".join(f"{x['from'][5:]}:{x['price_eur']}" for x in u["available_dates"])
    for slug, nb in new.items():
        nb["base_price_eur"] = min(x["price_eur"] for x in nb["available_dates"])
        out["new:yr-" + slug] = nb
    return out
