# -*- coding: utf-8 -*-
"""
Кураторский слой пайплайна: ВОСПРОИЗВОДИМЫЙ подбор кадров.
Читает clip_catalog + curation_<batch>.json и выдаёт по локации отранжированный пул
годных клипов, исключая: чужую локацию, человека в кадре, брак/статику (exclude),
и — на уровне ОКНА — секунды с дефектами и доворотами.

clean_window(clip, dur_sec): окно внутри HERO-сегмента, без секунд с флагами брака
(пересвет/провал/расфокус/рывок) и без доворотов (высокий turn/curl/jerk), с движением
к концу (к акценту, правило 10). Импортируется сборщиком assemble_from_plan.py.

CLI: python curate.py <batch> <location>   — печатает годный пул с чистыми окнами.
"""
import os, sys, json
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFECT_FLAGS = ("пересвет", "провал", "расфокус", "мыло", "рывок")


def load(batch):
    cur = json.load(open(os.path.join(ROOT, "work", f"curation_{batch}.json"), encoding="utf-8"))
    cat_p = os.environ.get("CLIP_CATALOG") or os.path.join(ROOT, "work", "clip_catalog.json")
    cat = json.load(open(cat_p, encoding="utf-8"))
    by_idx = {c["idx"]: c for c in cat["clips"]}
    return cur, by_idx


def bad_seconds(clip):
    """Множество секунд (t) с браком или доворотом — их избегаем в окне."""
    bad = set()
    for d in clip.get("defects", []):
        if any(any(k in f for k in DEFECT_FLAGS) for f in d.get("flags", [])):
            bad.add(d["t"])
    for s in clip.get("seconds", []):
        # доворот/рывок: высокий поворот камеры или крен или резкий рывок
        if s.get("jerk", 0) > 1.3 or s.get("curl", 0) > 0.9 or abs(s.get("turn", 0)) > 70:
            bad.add(s["t"])
    return bad


def hero_bounds(clip):
    """[(start,end)] hero-сегментов; если нет — весь клип как один интервал."""
    segs = [(sg["start"], sg["end"]) for sg in clip.get("segments", []) if sg["bucket"] == "hero"]
    return segs or [(0, int(clip["duration_sec"]))]


def _window_in_seg(clip, lo, hi, L, smooth_weight=1.0):
    """Доктрина stage4.best_window_of_length: окно длины L (сек) в [lo,hi) по КРАСОТЕ
    (sec_scores) МИНУС штрафы за рывок/старт-стоп/статику/мёртвый вход/спайк-выход/
    орбиту(curl)/доворот(ang). Возвращает (start_sec, raw_score, val)."""
    scores_all = clip.get("sec_scores") or []
    secs = clip.get("seconds") or []
    scores = scores_all[lo:hi]
    n = len(scores)
    if n == 0:
        return float(lo), 0.5, -1e9
    if n <= L:
        return float(lo), float(np.mean(scores)), -1e9
    by_t = {s["t"]: s for s in secs}
    best_off, best_val, best_raw = 0, -1e9, 0.0
    for off in range(0, n - L + 1):
        raw = float(np.mean(scores[off:off + L]))
        win = [by_t.get(lo + off + k) for k in range(L)]
        win = [x for x in win if x]
        penalty = 0.0
        if win:
            jerk_max = max(x["jerk"] for x in win)
            flows = [x["flow"] for x in win]
            f_min, f_max = min(flows), max(flows); f_mean = sum(flows) / len(flows)
            penalty += min(1.0, jerk_max / 1.2)
            if f_max / max(0.05, f_min) > 3.0:
                penalty += 0.5
            if f_mean < 1.2:
                penalty += min(0.8, (1.2 - f_mean) / 1.2 * 0.8)
            if len(win) >= 2:
                head2 = (win[0]["flow"] + win[1]["flow"]) / 2
                if win[0]["flow"] < 0.5:
                    penalty += 1.5
                elif head2 < 0.7:
                    penalty += 0.8
                tail = win[-1]["flow"]; body = sum(x["flow"] for x in win[:-1]) / max(1, len(win) - 1)
                if tail > body * 2.2 and tail > 3.0:
                    penalty += min(1.6, (tail / max(body, 0.5) - 1.0) * 0.5)
            # РЕЗКОСТЬ поворота (не факт облёта): угловое ускорение (плавный облёт не штрафуем)
            angs = [x.get("ang") for x in win if x.get("ang") is not None]
            if len(angs) > 2:
                omega = np.degrees(np.diff(np.unwrap(angs)))
                ang_accel = float(np.max(np.abs(np.diff(omega))))
            else:
                ang_accel = 0.0
            penalty += min(1.5, ang_accel / 40.0)
        val = raw - smooth_weight * penalty
        if val > best_val:
            best_val, best_off, best_raw = val, off, raw
    return float(lo + best_off), best_raw, best_val


def _score_window(clip, start, L):
    """Скор ОДНОГО окна [start,start+L): красота − штрафы (та же доктрина)."""
    scores_all = clip.get("sec_scores") or []; by_t = {s["t"]: s for s in clip.get("seconds") or []}
    seg = scores_all[int(start):int(start) + L]
    raw = float(np.mean(seg)) if seg else 0.5
    win = [by_t.get(int(start) + k) for k in range(L)]; win = [x for x in win if x]
    pen = 0.0
    if win:
        jerk_max = max(x["jerk"] for x in win)
        flows = [x["flow"] for x in win]; f_min, f_max = min(flows), max(flows); f_mean = sum(flows) / len(flows)
        pen += min(1.0, jerk_max / 1.2)
        if f_max / max(0.05, f_min) > 3.0: pen += 0.5
        if f_mean < 1.2: pen += min(0.8, (1.2 - f_mean) / 1.2 * 0.8)
        if len(win) >= 2:
            if win[0]["flow"] < 0.5: pen += 1.5
            elif (win[0]["flow"] + win[1]["flow"]) / 2 < 0.7: pen += 0.8
            tail = win[-1]["flow"]; body = sum(x["flow"] for x in win[:-1]) / max(1, len(win) - 1)
            if tail > body * 2.2 and tail > 3.0: pen += min(1.6, (tail / max(body, 0.5) - 1.0) * 0.5)
        angs = [x.get("ang") for x in win if x.get("ang") is not None]
        if len(angs) > 2:
            omega = np.degrees(np.diff(np.unwrap(angs))); pen += min(1.5, float(np.max(np.abs(np.diff(omega)))) / 40.0)
    return raw - pen


def clean_window_candidates(clip, dur_sec, topk=8):
    """ТОП-k окон-кандидатов (start, val) по доктрине, лучшие первыми. Нужны, чтобы
    сборщик мог декодировать кадр и выбрать лучшее ПО КОМПОЗИЦИИ среди чистых."""
    L = max(1, int(round(dur_sec))); cd = clip["duration_sec"]
    hi_cap = max(0.0, cd - dur_sec - 0.1)
    scores_all = clip.get("sec_scores") or []; secs = clip.get("seconds") or []
    by_t = {s["t"]: s for s in secs}
    cands = []
    for a, b in hero_bounds(clip):
        hi = min(int(b), int(cd)); lo = min(int(a), max(0, hi - 1))
        n = len(scores_all[lo:hi])
        for off in range(0, max(0, n - L) + 1):
            start = lo + off
            if start > hi_cap:
                continue
            cands.append((float(start), _score_window(clip, start, L)))
    cands.sort(key=lambda x: x[1], reverse=True)
    # прорежаем близкие старты (в пределах L), чтобы кандидаты были разнесены
    out = []
    for st, val in cands:
        if all(abs(st - o) >= L for o, _ in out):
            out.append((st, val))
        if len(out) >= topk:
            break
    return out or [(min(max(0.0, (cd - dur_sec) / 2), hi_cap), -1.0)]


def clean_window(clip, dur_sec):
    """Окно длиной dur_sec ПО ДОКТРИНЕ (красота sec_scores − штрафы за
    рывок/статику/старт-стоп/мёртвый вход/спайк/орбиту/доворот). Перебирает
    hero-сегменты, берёт глобально лучшее. ГАРАНТИЯ: start+dur_sec <= длит.клипа.
    Возвращает (start_sec, val)."""
    L = max(1, int(round(dur_sec)))
    cd = clip["duration_sec"]
    hi_cap = max(0.0, cd - dur_sec - 0.1)
    best = None
    for a, b in hero_bounds(clip):
        hi = min(int(b), int(cd)); lo = min(int(a), max(0, hi - 1))
        start, raw, val = _window_in_seg(clip, lo, hi, L)
        start = min(start, hi_cap)
        if best is None or val > best[2]:
            best = (start, raw, val)
    if best is None:
        return float(min(max(0.0, (cd - dur_sec) / 2), hi_cap)), -1.0
    return float(min(max(0.0, best[0]), hi_cap)), float(best[2])


def multiwindow_pool(clip, dur_sec, gap_sec=6.0, max_windows=3):
    """#7 (2026-08-08): ОДНО видео = МНОГО планов. Возвращает до max_windows ЧИСТЫХ окон
    одного клипа, разнесённых >= gap_sec (правило «части одного дубля разносить >6с»).
    Для подбора: длинный дубль даёт несколько слотов (токены idx:start)."""
    cands = clean_window_candidates(clip, dur_sec, topk=12)
    out = []
    for st, val in cands:
        if all(abs(st - o) >= gap_sec + dur_sec for o, _ in out):
            out.append((st, val))
        if len(out) >= max_windows:
            break
    return out


def eligible(batch, location):
    cur, by_idx = load(batch)
    loc = cur["locations"][location]["idx"]
    person = set(cur.get("person_in_frame", {}).get("idx", []))
    excl = set(int(k) for k in cur.get("exclude", {}).keys())
    rows = []
    for idx in loc:
        if idx in person or idx in excl or idx not in by_idx:
            continue
        cl = by_idx[idx]
        flows = [s["flow"] for s in cl["seconds"]] or [0]
        mf = sum(flows) / len(flows)
        has_hero = any(sg["bucket"] == "hero" for sg in cl["segments"])
        rows.append({"idx": idx, "file": cl["file"], "score": cl["mean_score"],
                     "mean_flow": round(mf, 2), "hero": has_hero,
                     "dur": cl["duration_sec"], "vertical": cl["vertical"]})
    rows.sort(key=lambda r: (r["hero"], r["score"], r["mean_flow"]), reverse=True)
    return cur, rows


def main():
    batch, location = sys.argv[1], sys.argv[2]
    cur, rows = eligible(batch, location)
    print(f"== {batch} / {location}: {cur['locations'][location]['title']} ==")
    print(f"Исключены: человек={cur.get('person_in_frame',{}).get('idx',[])}  "
          f"брак/статика={sorted(int(k) for k in cur.get('exclude',{}))}")
    print(f"Годный пул ({len(rows)} клипов), ранг по hero/score/motion:")
    print("idx  file          score  flow  hero  dur   чистое_окно(2.7с)")
    _, by_idx = load(batch)
    for r in rows:
        off, val = clean_window(by_idx[r["idx"]], 2.7)
        print(f"{r['idx']:3d}  {r['file'][:12]:12s}  {r['score']:.2f}  {r['mean_flow']:.2f}  "
              f"{'H' if r['hero'] else '.'}    {r['dur']:5.1f}  off={off:.0f}s")


if __name__ == "__main__":
    main()
