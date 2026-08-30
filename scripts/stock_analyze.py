# -*- coding: utf-8 -*-
"""
Стоковый анализ — отдельная ветка, НЕ трогает основной пайплайн монтажа.

Берёт горизонтальные (16:9) клипы из скана output/stock/_index/dji_scan.json,
выбирает топ-N поездок по числу горизонталок и прогоняет по ним проверенную
логику stage2 (посекундные метрики, детекция брака, нарезка на чистые
односценовые сегменты hero/filler).

Отличия от stage2:
  - только горизонталь (для стока вертикаль почти не продаётся);
  - порог сегмента = STOCK_MIN_SEC (5с, минимум стока), а не min_clip_sec;
  - каталог пишется per-trip в output/stock/<slug>/analysis/catalog.json,
    а не в work/clip_catalog.json (чтобы не затереть Вьетнам).

Запуск:
    python scripts\stock_analyze.py            # все выбранные поездки
    python scripts\stock_analyze.py --smoke 3  # по 3 клипа с поездки (проверка)
"""
import os, sys, json, time
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
# переиспользуем проверенную логику анализа
from stage2_analyze import analyze_clip, score_second, is_defective, find_segments

SCAN = os.path.join(ROOT, "output", "stock", "_index", "dji_scan.json")
STOCK_MIN_SEC = 5.0     # сток: минимальная длина клипа 5с
TOP_N_TRIPS = 3         # сколько поездок брать (по числу горизонталок)

_TR = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z',
    'и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
    'с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch',
    'ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
}
def slugify(name):
    out = []
    for ch in name.lower():
        if ch in _TR: out.append(_TR[ch])
        elif ch.isalnum(): out.append(ch)
        elif ch in ' -_.': out.append('_')
    s = ''.join(out)
    while '__' in s: s = s.replace('__', '_')
    return s.strip('_') or 'trip'


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_fps(fps):
    if isinstance(fps, (int, float)): return round(float(fps), 3)
    try:
        n, d = str(fps).split('/'); return round(int(n) / int(d), 3)
    except Exception:
        return None


def main():
    smoke = None
    if '--smoke' in sys.argv:
        smoke = int(sys.argv[sys.argv.index('--smoke') + 1])

    cfg = load_cfg()
    ffmpeg = cfg["tools"]["ffmpeg"]
    scan = json.load(open(SCAN, encoding="utf-8"))

    # горизонталь по поездкам
    horiz = [r for r in scan if r.get("horiz") and not r.get("err")]
    from collections import Counter
    counts = Counter(r["trip"] for r in horiz)
    top = [name for name, _ in counts.most_common(TOP_N_TRIPS)]

    print("=" * 70)
    print("СТОКОВЫЙ АНАЛИЗ — выбранные поездки (топ по горизонталкам):")
    for name in top:
        print(f"  • {name}: {counts[name]} горизонтальных клипов  -> slug '{slugify(name)}'")
    print("=" * 70, flush=True)

    grand = []
    for trip in top:
        clips = sorted([r for r in horiz if r["trip"] == trip], key=lambda r: r["file"])
        if smoke:
            clips = clips[:smoke]
        slug = slugify(trip)
        out_dir = os.path.join(ROOT, "output", "stock", slug, "analysis")
        os.makedirs(out_dir, exist_ok=True)

        catalog = {"trip": trip, "slug": slug, "clips": [], "skipped": []}
        t0 = time.time()
        print(f"\n### {trip} ({len(clips)} клипов) -> output/stock/{slug}/analysis/")
        for i, r in enumerate(clips):
            name = r["file"]; path = r["path"]; dur = r.get("dur") or 0
            print(f"  [{i+1:3d}/{len(clips)}] {name} ({dur:.0f}s)...", flush=True)
            secs, plan_cuts = analyze_clip(path, dur, ffmpeg, vertical=False)
            if not secs:
                catalog["skipped"].append({"file": name, "reason": "не открылся/пустой"})
                continue
            night = float(np.median([s["luma"] for s in secs])) < 55.0
            norm = {"sharp_hi": 250.0, "jerk_hi": 1.2, "color_hi": 45.0, "flow_hi": 3.0}
            scores = [score_second(s, norm, night) for s in secs]
            defects = [{"t": s["t"], "flags": f} for s in secs if (f := is_defective(s, night))]
            segments = find_segments(secs, scores, plan_cuts, STOCK_MIN_SEC, night)
            n_hero = sum(1 for sg in segments if sg["bucket"] == "hero")
            catalog["clips"].append({
                "idx": i, "file": name, "path": path,
                "trip": trip, "vertical": False,
                "duration_sec": round(dur, 2), "fps": parse_fps(r.get("fps")),
                "w": r.get("ew"), "h": r.get("eh"),
                "mean_score": round(float(np.mean(scores)), 3),
                "night": night,
                "plan_cuts": plan_cuts,
                "segments": segments,
                "defects": defects,
                "seconds": secs,
                "sec_scores": [round(x, 3) for x in scores],
            })
            print(f"        сегментов: {len(segments)} (hero {n_hero}), "
                  f"годного {sum(sg['dur'] for sg in segments):.1f}с из {dur:.0f}с", flush=True)

        out = os.path.join(out_dir, "catalog.json")
        json.dump(catalog, open(out, "w", encoding="utf-8"), ensure_ascii=False)

        segs = [(c["file"], sg) for c in catalog["clips"] for sg in c["segments"]]
        hero5 = [(f, sg) for f, sg in segs if sg["bucket"] == "hero"]
        dt = time.time() - t0
        summary = {
            "trip": trip, "slug": slug,
            "clips_analyzed": len(catalog["clips"]),
            "clips_skipped": len(catalog["skipped"]),
            "segments_total": len(segs),
            "segments_hero": len(hero5),
            "hero_dur_sec": round(sum(sg["dur"] for _, sg in hero5), 1),
            "seconds": round(dt, 0),
        }
        grand.append(summary)
        print(f"  -> {trip}: чистых сегментов {len(segs)} (hero {len(hero5)}), "
              f"hero-материала {summary['hero_dur_sec']:.0f}с. За {dt/60:.1f} мин", flush=True)

    # общий сводник
    sum_path = os.path.join(ROOT, "output", "stock", "_index", "analysis_summary.json")
    json.dump(grand, open(sum_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n" + "=" * 70)
    print("ИТОГО по стоку:")
    for s in grand:
        print(f"  {s['trip']:34s} hero-сегментов {s['segments_hero']:4d}  "
              f"({s['hero_dur_sec']:.0f}с годного)")
    print(f"Сводка -> output/stock/_index/analysis_summary.json")


if __name__ == "__main__":
    main()
