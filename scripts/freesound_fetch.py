# -*- coding: utf-8 -*-
"""
Наполнение библиотеки work/sfx/ с Freesound API (token-режим, HQ-превью mp3 —
для подложки −15дБ под музыкой достаточно). Только CC0 (коммерчески чисто для стоков).

Ключ: config.yaml -> sfx.freesound_token  (получить: freesound.org/apiv2/apply)
CLI: python freesound_fetch.py [тег ...]   (без аргументов — вся карта TAGS)
"""
import os, sys, json, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX = os.path.join(ROOT, "work", "sfx")

# тег -> (поисковый запрос, сколько файлов, мин-макс длительность сек)
TAGS = {
    "ocean":      ("ocean waves shore gentle", 3, 30, 180),
    "surf":       ("sea waves crashing beach", 2, 30, 180),
    "seagulls":   ("seagulls coast", 2, 20, 120),
    "wind":       ("mountain wind ambience soft", 3, 30, 180),
    "jungle":     ("jungle ambience cicadas tropical", 3, 30, 180),
    "birds":      ("morning birds forest ambience", 2, 30, 180),
    "waterfall":  ("waterfall ambience", 2, 30, 180),
    "river":      ("river stream water flowing", 2, 30, 180),
    "city_night": ("city night ambience distant traffic", 2, 30, 180),
    "marina":     ("harbor boats water lapping", 2, 20, 180),
    "fireworks":  ("fireworks distant crowd", 2, 20, 120),
    "boat":       ("sailboat water hull lapping", 2, 20, 180),
    "burner":    ("hot air balloon burner", 2, 5, 90),
    "wind_cold": ("cold winter wind mountains", 2, 30, 180),
}


def api(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_tag(tag, q, n, dmin, dmax, token):
    d = os.path.join(SFX, tag); os.makedirs(d, exist_ok=True)
    have = [f for f in os.listdir(d) if f.endswith(".mp3")]
    if len(have) >= n:
        print(f"  {tag}: уже {len(have)} файлов — пропуск"); return 0
    params = urllib.parse.urlencode({
        "query": q, "filter": f'license:"Creative Commons 0" duration:[{dmin} TO {dmax}]',
        "sort": "rating_desc", "fields": "id,name,previews,duration,username,avg_rating",
        "page_size": 15, "token": token})
    res = api(f"https://freesound.org/apiv2/search/text/?{params}", token)
    got = 0
    for s in res.get("results", []):
        if got + len(have) >= n:
            break
        url = s["previews"]["preview-hq-mp3"]
        safe = "".join(c for c in s["name"][:40] if c.isalnum() or c in " -_").strip() or str(s["id"])
        out = os.path.join(d, f"{s['id']}_{safe}.mp3")
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Token {token}"})
            with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
                f.write(r.read())
            print(f"  {tag}: +{os.path.basename(out)} ({s['duration']:.0f}с, рейт {s.get('avg_rating',0):.1f})")
            got += 1
        except Exception as e:
            print(f"  {tag}: {s['id']} не скачался: {e}")
    return got


def main():
    # ключ из ПРИВАТНОГО config.local.yaml (приоритет), затем config.yaml
    token = None
    for name in ("config.local.yaml", "config.yaml"):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            c = yaml.safe_load(open(p, encoding="utf-8")) or {}
            token = (c.get("sfx") or {}).get("freesound_token") or token
    if not token:
        print("НЕТ ключа: config.local.yaml -> sfx.freesound_token (freesound.org/apiv2/apply)"); return
    tags = sys.argv[1:] or list(TAGS.keys())
    total = 0
    for t in tags:
        q, n, dmin, dmax = TAGS[t]
        total += fetch_tag(t, q, n, dmin, dmax, token)
    print(f"Скачано {total} файлов -> work/sfx/")


if __name__ == "__main__":
    main()
