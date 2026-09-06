"""Общие помощники для фетчеров (файл с «_» не загружается как фетчер)."""
import html as _html
import json
import pathlib
import re
import time
from datetime import date

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 20
WINDOW = (date(2026, 11, 9), date(2026, 11, 24))


def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def get(s, url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    r = s.get(url, **kw)
    r.raise_for_status()
    return r


def post(s, url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    r = s.post(url, **kw)
    r.raise_for_status()
    return r


def load_ids():
    return json.load(open(ROOT / "sources" / "ids.json", encoding="utf-8"))


def priority_ids(boats):
    """Лодки из watchlist (priority ∪ references), существующие в snapshot."""
    wl = json.load(open(ROOT / "state" / "watchlist.json", encoding="utf-8"))
    ids = list(dict.fromkeys(wl.get("priority", []) + wl.get("references", [])))
    return [b for b in ids if b in boats]


def slot(frm, to, price_eur):
    a, b = date.fromisoformat(frm), date.fromisoformat(to)
    return {"from": frm, "to": to, "days": (b - a).days, "price_eur": price_eur}


def in_window(frm, to):
    a, b = date.fromisoformat(frm), date.fromisoformat(to)
    return a >= WINDOW[0] and b <= WINDOW[1] and (b - a).days >= 6


def text(s):
    """HTML → плоский текст с разделителем «|» между тегами."""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.S)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(\|\s*)+", "|", t)
    return _html.unescape(t)


def eur(s):
    """'9.966 €' / '11,725' / '€10,500' → int или None."""
    m = re.search(r"([\d][\d.,\s]*)", s or "")
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return int(digits) if digits else None


def pause(sec):
    time.sleep(sec)


def new_boat(**kw):
    """Заготовка новой лодки по схеме snapshot (неизвестное = null)."""
    base = {"type": "catamaran", "bareboat": True, "model": None, "yacht_name": None, "year": None,
            "length_ft": None, "guest_cabins_double": None, "extra_cabins": None,
            "total_berths_incl_saloon": None, "saloon_berth": None, "toilets": None, "base": None,
            "operator": None, "operator_rating": None, "condition_score": None, "watermaker": None,
            "equipment": {k: None for k in ("generator", "air_conditioning", "electric_winch", "inverter",
                                            "large_fridge", "good_swim_platform",
                                            "flybridge_or_good_cockpit", "solar")},
            "available_dates": [], "booking_status": "unknown", "base_price_eur": None,
            "base_price_note": None, "mandatory_extras": [], "mandatory_extras_complete": False,
            "deposit_eur": None, "verified": False, "verification_note": None, "penalties": [],
            "source_ids": {}, "urls": [], "role": None}
    base.update(kw)
    return base
