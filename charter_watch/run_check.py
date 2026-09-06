#!/usr/bin/env python3
"""Скриптовый прогон: запускает фетчеры из sources/, обновляет snapshot и печатает
компактную сводку. Агент читает ТОЛЬКО вывод этого скрипта, а не snapshot целиком.

Использование:  python3 run_check.py [--only 12knots,boataround] [--dry]

Контракт фетчера (файл sources/<name>.py):
    NAME = "12knots"
    def fetch(boats: dict, windows: list[tuple[str, str]]) -> dict
        # возвращает {boat_id: {"available_dates": [...], "base_price_eur": ..,
        #   "mandatory_extras": [...], "deposit_eur": .., "booking_status": ..,
        #   "source_note": "...", "raw_urls": [...]}}
        # Только те поля, которые источник реально отдаёт. Ничего не выдумывать.
        # Новые лодки: ключ "new:<slug>" со всеми известными полями.
Фетчер, упавший с исключением, не валит прогон — фиксируется в sources_checked.
"""
import importlib.util
import json
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent
SNAP = ROOT / "state" / "snapshot.json"
WINDOW = (date(2026, 11, 9), date(2026, 11, 24))
WINDOWS = [((WINDOW[0] + timedelta(d)).isoformat(), (WINDOW[0] + timedelta(d + 7)).isoformat())
           for d in range(0, 9)]


def load_fetchers(only=None):
    out = []
    for f in sorted((ROOT / "sources").glob("*.py")):
        if f.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f.stem, f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        name = getattr(m, "NAME", f.stem)
        if only and name not in only:
            continue
        out.append((name, m))
    return out


def merge(boat, upd, name, ts):
    """Обновить поля лодки данными источника. Даты — объединение по (from,to)."""
    if "available_dates" in upd:
        seen = {(s["from"], s["to"]): s for s in boat.get("available_dates", []) if s.get("source") != name}
        for s in upd["available_dates"]:
            s = dict(s, source=name)
            seen[(s["from"], s["to"])] = s
        boat["available_dates"] = sorted(seen.values(), key=lambda s: s["from"])
    for k in ("base_price_eur", "mandatory_extras", "mandatory_extras_complete", "deposit_eur",
              "booking_status", "watermaker", "guest_cabins_double", "total_berths_incl_saloon"):
        if k in upd and upd[k] is not None:
            boat[k] = upd[k]
    notes = boat.setdefault("source_notes", {})
    if upd.get("source_note"):
        notes[name] = upd["source_note"]
    for u in upd.get("raw_urls", []):
        if u not in boat.setdefault("urls", []):
            boat["urls"].append(u)
    boat["timestamp"] = ts


def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    dry = "--dry" in sys.argv
    snap = json.load(open(SNAP))
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    checked = {}
    for name, m in load_fetchers(only):
        try:
            res = m.fetch(snap["boats"], WINDOWS)
            n = 0
            for bid, upd in res.items():
                if bid.startswith("new:"):
                    slug = bid[4:]
                    if slug not in snap["boats"]:
                        snap["boats"][slug] = dict(upd, verified=False, timestamp=ts,
                                                   verification_note=f"обнаружена {name} {ts}")
                    continue
                if bid in snap["boats"]:
                    merge(snap["boats"][bid], upd, name, ts)
                    n += 1
            checked[name] = f"ok: {n} лодок обновлено"
        except Exception as e:  # noqa: BLE001
            checked[name] = f"fail: {type(e).__name__}: {str(e)[:120]}"
    snap["sources_checked"] = {**snap.get("sources_checked", {}), **checked}
    snap["generated_at"] = ts
    if not dry:
        json.dump(snap, open(SNAP, "w"), ensure_ascii=False, indent=1)

    sys.path.insert(0, str(ROOT))
    import compare
    print(f"run_check {ts}  фетчеров: {len(checked)}")
    for k, v in checked.items():
        print(f"  {k}: {v}")
    print("\nКандидаты (hard OK, есть даты в окне):")
    rows = []
    for bid, b in snap["boats"].items():
        ok, _ = compare.hard_filter(b)
        sl = compare.slots_in_window(b)
        if ok and sl:
            t, c = compare.total_price(b)
            rows.append((compare.score(b), bid, b.get("model"), b.get("year"),
                         f"€{t:,.0f}{'' if c else '?'}" if t else "?", b.get("booking_status"),
                         ",".join(s["from"][5:] for s in sl[:5]), "V" if b.get("verified") else "-"))
    for r in sorted(rows, reverse=True):
        print("  " + " | ".join(str(x) for x in r))
    print("\nБез дат в окне, но проходят фильтры (WATCH):",
          ", ".join(bid for bid, b in snap["boats"].items()
                    if compare.hard_filter(b)[0] and not compare.slots_in_window(b)))


if __name__ == "__main__":
    main()
