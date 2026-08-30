# -*- coding: utf-8 -*-
"""
Этап 2 — Анализ материала, детекция брака и нарезка на чистые отрезки.

По каждому клипу из work/clip_index.json:
  - посекундные метрики: резкость, экспозиция, движение/рывки, «сочность» (как раньше);
  - детект смены плана/света ВНУТРИ клипа (корреляция HSV-гистограммы между
    соседними секундами) — длинный дубль, где меняется композиция или свет,
    режется на отдельные под-планы вместо одного среднего скора на весь файл;
  - брак (дрожь/пересвет/провал/расфокус) режет отрезок, как это вручную делает
    оператор — а не просто занижает скор всего файла.

Результат — не «одно лучшее окно на файл», а СПИСОК чистых отрезков произвольной
длины (>= edit.min_clip_sec), каждый размечен как hero (нашёл смену плана/света
внутри исходного дубля — самостоятельно интересный, разноплановый материал) или
filler (одна статичная композиция — годится на подмес/связки).

Выход: work/clip_catalog.json
  по клипу: segments[] (start/end/score/bucket/причина), defects[], посекундные
  метрики (seconds/sec_scores) — для последующего окна произвольной длины внутри
  сегмента (см. stage4_reels_music.py).

Исходники только читаются. Долгий прогон — пишет прогресс в stdout.

Точечный прогон (для проверки перед полным батчем):
    python stage2_analyze.py 21,38,30
"""
import os, sys, json, time
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import yaml
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE_FPS = 2          # сколько выборок в секунду
ANALYZE_W = 320         # ширина кадра для анализа
MAX_ANALYZE_SEC = 300   # потолок анализа на клип (был 150 — резал длинные облёты,
                        # напр. idx38 DJI_0878 = 280с, целиком помещается теперь)
DRIFT_CORR_THRESHOLD = 0.75  # корреляция HSV-гистограммы с "якорем" начала текущего
                              # плана; ниже — план уже визуально другой (не соседний
                              # кадр — дрон меняет композицию ПЛАВНО, а не склейкой,
                              # поэтому сравниваем с якорем, а не с предыдущим кадром)
DRIFT_MIN_GAP_SEC = 4.0       # не резать чаще чем раз в N секунд (иначе одна и та же
                              # смена плана ловится по 3-5 раз подряд, пока не осядет)
MIN_SEGMENT_FOR_HERO = 5.0  # от скольки секунд сегмент может претендовать на hero
                             # по одной только длительности (без смены плана внутри)


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def colorfulness(img):
    """Метрика Hasler-Susstrunk: насколько кадр «цветной»."""
    b, g, r = img[..., 0].astype(np.float32), img[..., 1].astype(np.float32), img[..., 2].astype(np.float32)
    rg = r - g
    yb = 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def hsv_hist(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [12, 12], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).flatten()


def analyze_clip(path, dur_hint, ffmpeg, vertical):
    """Декод через ffmpeg-пайп: многопоточно, сразу 2fps + даунскейл до 320px.
    Заодно считаем HSV-гистограмму кадра — для детекта смены плана внутри клипа
    (без второго прохода декодирования)."""
    import subprocess as sp
    if vertical:
        w_out, h_out = 180, 320
    else:
        w_out, h_out = 320, 180
    t_cap = min(dur_hint or MAX_ANALYZE_SEC, MAX_ANALYZE_SEC)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-t", f"{t_cap:.2f}", "-i", path,
           "-vf", f"fps={SAMPLE_FPS},scale={w_out}:{h_out}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    try:
        proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, bufsize=10 ** 7)
    except Exception:
        return None

    frame_bytes = w_out * h_out * 3
    samples = []   # (t_sec, sharp, over, under, luma, color, flow_mag, jerk)
    plan_cuts = []  # моменты смены плана/света внутри клипа (дрейф от якоря)
    prev_gray = None
    prev_flow_mean = None
    anchor_hist = None
    last_cut_t = -999.0
    k = 0
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        small = np.frombuffer(buf, np.uint8).reshape(h_out, w_out, 3)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        t = k / SAMPLE_FPS

        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        luma = float(gray.mean())
        over = float((gray > 250).mean() * 100)
        under = float((gray < 5).mean() * 100)
        colf = colorfulness(small)

        flow_mag = 0.0
        jerk = 0.0
        flow_ang = 0.0    # доминирующее направление потока (рад) — для детекта доворота
        curl = 0.0        # вращательная компонента поля — орбита/крен камеры
        if prev_gray is not None:
            flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None,
                                                0.5, 2, 12, 2, 5, 1.1, 0)
            u, v = flow[..., 0], flow[..., 1]
            fm = float(np.sqrt(u ** 2 + v ** 2).mean())
            flow_mag = fm
            if prev_flow_mean is not None:
                jerk = abs(fm - prev_flow_mean)
            prev_flow_mean = fm
            # направление и вращение считаем только там, где есть движение
            mask = (np.hypot(u, v) > 0.3)
            if mask.sum() > 30:
                flow_ang = float(np.arctan2(v[mask].mean(), u[mask].mean()))
                hh, ww = gray.shape
                yy, xx = np.mgrid[0:hh, 0:ww]
                rx, ry = xx - ww / 2.0, yy - hh / 2.0
                tang = rx * v - ry * u                 # z-компонента r x flow
                rr = np.sqrt(rx ** 2 + ry ** 2) + 1e-6
                curl = float((tang[mask] / rr[mask]).mean())
        prev_gray = gray

        # смена плана/света внутри клипа: дрон меняет композицию ПЛАВНО (не
        # склейкой), поэтому сравниваем с "якорем" начала текущего плана, а не
        # с предыдущим кадром — иначе плавный дрейф никогда не поймать
        hist = hsv_hist(small)
        if anchor_hist is None:
            anchor_hist = hist
        else:
            c = float(cv2.compareHist(anchor_hist.astype(np.float32),
                                      hist.astype(np.float32), cv2.HISTCMP_CORREL))
            if c < DRIFT_CORR_THRESHOLD:
                if (t - last_cut_t) >= DRIFT_MIN_GAP_SEC:
                    plan_cuts.append(round(t, 1))
                    last_cut_t = t
                anchor_hist = hist  # якорь двигаем в любом случае — не копим дрейф

        samples.append((t, sharp, over, under, luma, colf, flow_mag, jerk, flow_ang, curl))
        k += 1
    proc.stdout.close()
    proc.wait()
    if not samples:
        return None, []

    arr = np.array(samples)  # t, sharp, over, under, luma, colf, flow, jerk, ang, curl
    t_end = arr[:, 0].max()
    seconds = []
    for s in range(int(t_end) + 1):
        m = arr[(arr[:, 0] >= s) & (arr[:, 0] < s + 1)]
        if len(m) == 0:
            continue
        # доворот камеры внутри секунды = размах направления потока (unwrap угла)
        angs = m[:, 8]
        turn = float(np.degrees(np.abs(np.diff(np.unwrap(angs))).sum())) if len(angs) > 1 else 0.0
        seconds.append({
            "t": s,
            "sharp": round(float(m[:, 1].mean()), 1),
            "over_pct": round(float(m[:, 2].mean()), 2),
            "under_pct": round(float(m[:, 3].mean()), 2),
            "luma": round(float(m[:, 4].mean()), 1),
            "color": round(float(m[:, 5].mean()), 1),
            "flow": round(float(m[:, 6].mean()), 3),
            "jerk": round(float(m[:, 7].mean()), 3),
            "turn": round(turn, 1),                       # доворот внутри секунды, град
            "ang": round(float(np.arctan2(np.sin(angs).mean(), np.cos(angs).mean())), 3),  # средний угол потока (рад)
            "curl": round(float(np.abs(m[:, 9]).mean()), 3),  # вращение поля (орбита/крен)
        })
    return seconds, plan_cuts


def score_second(s, norm, night=False):
    """Скоринг секунды 0..1: резкость + экспозиция + плавность + сочность.
    night: ночной клип — темнота НЕ штраф (ночью это норма), важнее пересвет
    (выбитые огни) и резкость самих огней."""
    sharp_n = min(1.0, s["sharp"] / (norm["sharp_hi"] * (0.4 if night else 1.0)))
    expo_ok = 1.0
    if s["over_pct"] > 2.0:
        expo_ok -= min(0.6, (s["over_pct"] - 2.0) / 10)
    if not night:
        if s["under_pct"] > 5.0:
            expo_ok -= min(0.6, (s["under_pct"] - 5.0) / 20)
        if s["luma"] < 30 or s["luma"] > 225:
            expo_ok -= 0.3
    else:
        # ночью штраф только за пересвет и за СЛИШКОМ тёмный кадр без огней
        if s["luma"] < 6:
            expo_ok -= 0.3
    smooth = 1.0 - min(1.0, s["jerk"] / norm["jerk_hi"])
    color_n = min(1.0, s["color"] / norm["color_hi"])
    motion_bonus = 0.5 + 0.5 * min(1.0, s["flow"] / norm["flow_hi"]) if s["flow"] > 0.15 else 0.3
    return max(0.0, 0.30 * sharp_n + 0.25 * expo_ok + 0.20 * smooth +
               0.15 * color_n + 0.10 * motion_bonus)


def is_defective(s, night=False):
    """Секунда — брак (дрожь/пересвет/провал/расфокус). Разрезает чистый отрезок.
    night: ночной клип — «недосвет» НЕ брак (тёмное небо/вода штатны ночью),
    порог резкости ниже (тёмные зоны дают низкую вариацию Лапласиана не из-за
    расфокуса). Без этого весь ночной город режется в труху (0 сегментов)."""
    flags = []
    if s["sharp"] < (12 if night else 40):
        flags.append("расфокус/мыло")
    if s["over_pct"] > 4:
        flags.append("пересвет")
    if not night and s["under_pct"] > 15:
        flags.append("провал")
    if s["jerk"] > 1.5:
        flags.append("рывок")
    return flags


def find_segments(secs, scores, plan_cuts, min_clip_sec, night=False):
    """Режем клип на чистые отрезки: рвём на браке И на смене плана/света внутри
    дубля (plan_cuts, см. analyze_clip). Возвращает список сегментов >= min_clip_sec,
    каждый помечен hero/filler.

    hero — сегмент из дубля, где обнаружена смена плана/света (разноплановый,
    «легко брать в работу» — так вы и категоризируете вручную), ЛИБО длинный
    (>= MIN_SEGMENT_FOR_HERO) участок с движением выше среднего.
    filler — одна статичная композиция, короче — на подмес/связки.
    """
    n = len(secs)
    if n == 0:
        return []

    # 1. рвём на браке (как раньше)
    runs = []  # список списков индексов секунд одного непрерывного чистого прогона
    cur = []
    for i, s in enumerate(secs):
        if is_defective(s, night):
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(i)
    if cur:
        runs.append(cur)

    cut_set = set(plan_cuts)
    segments = []
    for run in runs:
        # 2. внутри чистого прогона — точки смены плана/света, посчитанные заранее
        cut_points = [i for i in run[1:] if secs[i]["t"] in cut_set]
        multi_plan = len(cut_points) > 0

        sub_runs, sub = [], []
        for i in run:
            if sub and i in cut_points:
                sub_runs.append(sub)
                sub = []
            sub.append(i)
        if sub:
            sub_runs.append(sub)

        for sub_run in sub_runs:
            start_i, end_i = sub_run[0], sub_run[-1]
            dur = secs[end_i]["t"] + 1 - secs[start_i]["t"]
            if dur < min_clip_sec:
                continue
            seg_scores = [scores[i] for i in sub_run]
            seg_flow = [secs[i]["flow"] for i in sub_run]
            score = float(np.mean(seg_scores))
            is_hero = multi_plan or (dur >= MIN_SEGMENT_FOR_HERO and float(np.mean(seg_flow)) > 1.2)
            reason = ("смена плана/света внутри дубля" if multi_plan else
                      "длинный динамичный отрезок" if is_hero else
                      "статичная композиция")
            segments.append({
                "start": secs[start_i]["t"], "end": secs[end_i]["t"] + 1,
                "dur": round(dur, 1), "score": round(score, 3),
                "bucket": "hero" if is_hero else "filler", "bucket_reason": reason,
            })
    return segments


def main():
    cfg = load_cfg()
    with open(os.path.join(ROOT, "work", "clip_index.json"), "r", encoding="utf-8") as f:
        index = json.load(f)

    only_idx = None
    if len(sys.argv) > 1:
        only_idx = set(int(x) for x in sys.argv[1].split(","))

    min_clip_sec = cfg["edit"]["min_clip_sec"]
    ffmpeg = cfg["tools"]["ffmpeg"]
    catalog = {"clips": [], "skipped": []}
    clips_to_run = [c for c in index["clips"] if only_idx is None or c["idx"] in only_idx]
    t0 = time.time()
    for c in clips_to_run:
        name = c["file"]
        print(f"[{c['idx']:3d}/{index['count']}] {name} ({c['duration_sec']}s)...", flush=True)
        secs, plan_cuts = analyze_clip(c["path"], c["duration_sec"], ffmpeg, c["h"] > c["w"])
        if not secs:
            catalog["skipped"].append({"idx": c["idx"], "file": name, "reason": "не открылся/пустой"})
            continue

        # НОЧНОЙ клип: медиана яркости низкая -> темнота не брак (ночной город/
        # салют). Иначе весь материал режется в 0 сегментов «недосветом».
        night = float(np.median([s["luma"] for s in secs])) < 55.0
        norm = {"sharp_hi": 250.0, "jerk_hi": 1.2, "color_hi": 45.0, "flow_hi": 3.0}
        scores = [score_second(s, norm, night) for s in secs]

        defects = []
        for s in secs:
            flags = is_defective(s, night)
            if flags:
                defects.append({"t": s["t"], "flags": flags})

        segments = find_segments(secs, scores, plan_cuts, min_clip_sec, night)
        n_hero = sum(1 for sg in segments if sg["bucket"] == "hero")

        entry = {
            "idx": c["idx"], "file": name, "path": c["path"],
            "vertical": c["h"] > c["w"],
            "duration_sec": c["duration_sec"], "fps": c["fps"],
            "mean_score": round(float(np.mean(scores)), 3),
            "plan_cuts": plan_cuts,
            "segments": segments,
            "defects": defects,
            "seconds": secs,
            "sec_scores": [round(x, 3) for x in scores],
        }
        catalog["clips"].append(entry)
        print(f"      сегментов: {len(segments)} (hero: {n_hero}, filler: {len(segments)-n_hero}), "
              f"годного материала: {sum(sg['dur'] for sg in segments):.1f}с из {c['duration_sec']}с")

    out = os.path.join(ROOT, "work", "clip_catalog.json")
    if only_idx and os.path.exists(out):
        # точечный прогон — обновляем только затронутые клипы, остальное не трогаем
        with open(out, "r", encoding="utf-8") as f:
            existing = json.load(f)
        by_idx = {e["idx"]: e for e in existing["clips"]}
        for e in catalog["clips"]:
            by_idx[e["idx"]] = e
        catalog = {"clips": list(by_idx.values()), "skipped": existing.get("skipped", [])}

    with open(out, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False)
    dt = time.time() - t0
    print(f"\nГотово за {dt/60:.1f} мин -> {os.path.relpath(out, ROOT)}")
    print(f"Каталогизировано: {len(catalog['clips'])}, пропущено: {len(catalog['skipped'])}")

    all_segs = [(c["idx"], c["file"], sg) for c in catalog["clips"] for sg in c["segments"]]
    total_dur = sum(sg["dur"] for _, _, sg in all_segs)
    n_hero = sum(1 for _, _, sg in all_segs if sg["bucket"] == "hero")
    print(f"\nВсего чистых сегментов: {len(all_segs)} ({total_dur:.0f}с материала), "
          f"hero: {n_hero}, filler: {len(all_segs)-n_hero}")


if __name__ == "__main__":
    main()
