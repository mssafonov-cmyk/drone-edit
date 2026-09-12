#!/usr/bin/env python3
"""Сравнение snapshot'ов лодок и детекция важных событий.

Использование:
  python3 compare.py state/history/<prev>.json state/snapshot.json [--json]

Печатает список событий (NEW / RELEASED / PRICE_DROP / BETTER_OPTION / PREMIUM_DEAL)
и черновики уведомлений. Код выхода 0 — есть события, 3 — событий нет.
Все правила — из CRITERIA.md. Скрипт детерминированный; агент не должен
"на глаз" решать, было ли событие.
"""
import json
import os
import sys
from datetime import date

BUDGET_EUR = 11000
WINDOW = (date(2026, 11, 9), date(2026, 11, 24))
PRICE_DROP_PCT = 0.07
PREMIUM_MODELS = ("elba 45", "saona 47", "lagoon 46", "lagoon 50", "lagoon 51",
                  "bali 4.6", "bali 4.8", "bali 5.4", "lagoon 450", "aura 51", "tanna 47")
REFERENCE_IDS = ("lola-2", "karysta")
WATCHLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "watchlist.json")


def held():
    """Удерживаемая (забронированная нами) лодка из watchlist.json → dict или None.

    Для неё логика инвертирована: занятое окно — норма, освободившееся окно —
    сигнал тревоги HOLD_LOST (опция слетела), а не хорошая новость RELEASED.
    """
    try:
        h = json.load(open(WATCHLIST)).get("held")
    except (OSError, ValueError):
        return None
    return h if h and h.get("boat_id") and h.get("window") else None


def hold_free(boat, win):
    """Окно удержания снова выглядит доступным у источников?"""
    if boat.get("booking_status") in ("available", "option"):
        for slot in boat.get("available_dates", []):
            if slot.get("from") == win["from"] and slot.get("to") == win["to"]:
                return True, f"окно {win['from']}…{win['to']} снова в выдаче как свободное"
        if boat.get("booking_status") == "available" and not boat.get("available_dates"):
            return False, ""
    return False, ""


def d(s):
    return date.fromisoformat(s) if s else None


def slots_in_window(boat):
    """Доступные интервалы (>=6 дней) целиком внутри окна."""
    out = []
    for slot in boat.get("available_dates", []):
        a, b = d(slot.get("from")), d(slot.get("to"))
        if not a or not b:
            continue
        if a >= WINDOW[0] and b <= WINDOW[1] and (b - a).days >= 6:
            out.append(slot)
    return out


def total_price(boat):
    """Известная обязательная стоимость: base + известные mandatory extras (EUR)."""
    base = boat.get("base_price_eur")
    if base is None:
        return None, False
    extras = sum(x.get("eur", 0) or 0 for x in boat.get("mandatory_extras", []))
    complete = bool(boat.get("mandatory_extras_complete"))
    return base + extras, complete


def hard_filter(boat):
    """(passes, reasons). None в поле = неизвестно, не отбрасываем, только помечаем."""
    reasons = []
    if boat.get("type", "catamaran") != "catamaran":
        reasons.append("не катамаран")
    if boat.get("bareboat") is False:
        reasons.append("не bareboat")
    gc = boat.get("guest_cabins_double")
    if gc is not None and gc < 4:
        reasons.append(f"гостевых двухместных кают {gc} < 4")
    berths = boat.get("total_berths_incl_saloon")
    if berths is not None and berths < 9:
        reasons.append(f"спальных мест {berths} < 9")
    if boat.get("watermaker") is False:
        reasons.append("нет watermaker")
    total, complete = total_price(boat)
    if total is not None and total > BUDGET_EUR and complete:
        reasons.append(f"обязательный итог €{total:,.0f} > €{BUDGET_EUR:,}")
    return (not reasons), reasons


def score(boat):
    """Эвристический скоринг 0–100 по весам из CRITERIA.md."""
    y = boat.get("year") or 2012
    cond = boat.get("condition_score")  # 0..10, если есть оценка/отзывы
    year_pts = max(0, min(10, (y - 2012) * 1.0))
    tech = 25 * ((year_pts if cond is None else (year_pts + cond) / 2) / 10)

    gc = boat.get("guest_cabins_double") or 0
    heads = boat.get("toilets") or 0
    saloon = boat.get("saloon_berth") is True
    comfort = 0
    comfort += 8 if gc >= 4 else 0
    comfort += 4 if gc >= 5 else 0
    comfort += min(4, heads)
    comfort += 4 if saloon else 0
    comfort = 20 * min(comfort, 20) / 20

    total, _ = total_price(boat)
    if total is None:
        value = 10  # неизвестно — середина
    else:
        value = 20 * max(0.0, min(1.0, (BUDGET_EUR * 1.15 - total) / (BUDGET_EUR * 0.6)))

    eq = boat.get("equipment", {})
    eq_pts = sum(2 for k in ("generator", "air_conditioning", "electric_winch",
                             "inverter", "large_fridge", "good_swim_platform",
                             "flybridge_or_good_cockpit") if eq.get(k))
    equipment = 15 * min(eq_pts, 14) / 14

    L = boat.get("length_ft") or 45
    handling = 10 if 40 <= L <= 50 else (6 if L <= 52 else 3)

    op = boat.get("operator_rating")  # 0..10
    operator = 10 * ((op if op is not None else 6) / 10)

    s = tech + comfort + value + equipment + handling + operator
    for pen in boat.get("penalties", []):
        s -= pen.get("points", 0)
    return int(round(max(0, min(100, s))))


def is_candidate(boat):
    ok, _ = hard_filter(boat)
    return ok and bool(slots_in_window(boat)) and boat.get("booking_status") != "booked"


def detect(prev, cur):
    events = []
    pb, cb = prev.get("boats", {}), cur.get("boats", {})
    h = held()
    hid = h["boat_id"] if h else None
    if h and hid in cb:
        free, why = hold_free(cb[hid], h["window"])
        was_free, _ = hold_free(pb.get(hid, {}), h["window"]) if hid in pb else (False, "")
        if free and not was_free:
            events.append(("HOLD_LOST", hid, score(cb[hid]), why))
    # Baseline без верифицированных лодок: любое "появление" — заполнение, а не событие.
    if not any(b.get("verified") for b in pb.values()):
        return events
    prev_leaders = [score(b) for b in pb.values() if is_candidate(b) and b.get("verified")]
    top_prev = max(prev_leaders) if prev_leaders else 0

    for bid, b in cb.items():
        if bid == hid:
            continue  # удерживаемая лодка — только через HOLD_LOST выше
        if not is_candidate(b):
            continue
        sc = score(b)
        p = pb.get(bid)
        total, complete = total_price(b)
        model = (b.get("model") or "").lower()

        if p is None:
            if not b.get("verified"):
                continue  # неверифицированная новая лодка — не событие, просто WATCH
            events.append(("NEW", bid, sc, "новая подходящая лодка в выдаче"))
        else:
            if not is_candidate(p) and p.get("booking_status") == "booked":
                events.append(("RELEASED", bid, sc, "ранее занятая лодка освободилась в окне"))
            elif not slots_in_window(p) and slots_in_window(b) and p.get("verified") \
                    and p.get("booking_status") in ("booked", "option"):
                events.append(("RELEASED", bid, sc, "появились даты внутри 9–24 ноября"))
            elif p.get("booking_status") == "option" and b.get("booking_status") == "available" \
                    and b.get("verified"):
                events.append(("RELEASED", bid, sc, "снята опция (on hold → available)"))
            pt, _ = total_price(p)
            if pt and total:
                if total <= pt * (1 - PRICE_DROP_PCT):
                    events.append(("PRICE_DROP", bid, sc,
                                   f"цена €{pt:,.0f} → €{total:,.0f} (−{(1 - total / pt) * 100:.0f}%)"))
                elif pt > BUDGET_EUR >= total:
                    events.append(("PRICE_DROP", bid, sc, f"вошла в бюджет: €{pt:,.0f} → €{total:,.0f}"))

        if top_prev and b.get("verified") and sc >= top_prev + 3 and bid not in REFERENCE_IDS \
                and (p is None or score(p) < top_prev + 3):
            events.append(("BETTER_OPTION", bid, sc, f"score {sc} > лидер {top_prev}"))

        if any(m in model for m in PREMIUM_MODELS) and (b.get("year") or 0) >= 2019 \
                and total is not None and total <= BUDGET_EUR and b.get("verified") \
                and (p is None or (total_price(p)[0] or 1e9) > BUDGET_EUR):
            events.append(("PREMIUM_DEAL", bid, sc, f"{b.get('model')} {b.get('year')} за €{total:,.0f}"))
    return events


def draft_hold_lost(boat, why, win):
    return "\n".join([
        "🚨 HOLD LOST — опция на нашу лодку больше не держится",
        "",
        f"{boat.get('model')} — {boat.get('yacht_name')} ({boat.get('year')})",
        f"Окно удержания: {win['from']} … {win['to']}",
        f"Оператор: {boat.get('operator')}",
        "",
        f"Что видно: {why}",
        f"Статус у источников: {boat.get('booking_status')}",
        "",
        "Это НЕ хорошая новость: лодку, которую мы держим, могут перехватить.",
        "Recommendation: немедленно связаться с оператором и подтвердить опцию/оплату.",
        "Sources:",
        *[f"- {u}" for u in boat.get("urls", [])[:4]],
    ])


def draft(kind, boat, sc, why):
    total, complete = total_price(boat)
    slots = ", ".join(f"{s['from'][5:]}…{s['to'][5:]}" for s in slots_in_window(boat)[:3])
    eq = boat.get("equipment", {})
    yn = lambda v: "✅" if v else ("❓" if v is None else "❌")
    tp = "€{:,.0f}{}".format(total, "" if complete else " (требует уточнения)") if total else "цена требует уточнения"
    return "\n".join([
        f"🔥 {kind}",
        "",
        f"{boat.get('model')} — {boat.get('yacht_name')}",
        f"{boat.get('year')} | {boat.get('length_ft')} ft | {boat.get('guest_cabins_double')} guest cabins"
        f"{' + saloon' if boat.get('saloon_berth') else ''} | {boat.get('total_berths_incl_saloon')} berths",
        f"{slots} Nov 2026",
        "",
        f"Total: {tp}",
        f"Deposit: €{boat.get('deposit_eur') or '?'}",
        "",
        f"Watermaker {yn(boat.get('watermaker'))}",
        f"Generator {yn(eq.get('generator'))}",
        f"AC {yn(eq.get('air_conditioning'))}",
        f"Saloon berth confirmed {yn(boat.get('saloon_berth'))}",
        "",
        f"Operator: {boat.get('operator')}",
        f"Score: {sc}/100",
        "",
        f"Почему: {why}",
        "Sources:",
        *[f"- {u}" for u in boat.get("urls", [])[:4]],
    ])


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    prev = json.load(open(sys.argv[1]))
    cur = json.load(open(sys.argv[2]))
    events = detect(prev, cur)
    if "--json" in sys.argv:
        print(json.dumps([{"kind": k, "boat_id": b, "score": s, "why": w} for k, b, s, w in events],
                         ensure_ascii=False, indent=2))
    else:
        if not events:
            print("Существенных событий нет.")
        h = held()
        for k, b, s, w in events:
            if k == "HOLD_LOST":
                print(draft_hold_lost(cur["boats"][b], w, h["window"]))
            else:
                print(draft(k, cur["boats"][b], s, w))
            print("\n" + "-" * 40 + "\n")
    sys.exit(0 if events else 3)


if __name__ == "__main__":
    main()
