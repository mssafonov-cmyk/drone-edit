# -*- coding: utf-8 -*-
"""
Этап 4 (рилсы) — сборка вертикальных рилсов из каталога этапа 2.

Логика:
  - Берём только нативно вертикальные клипы (9:16), кроп не нужен.
  - Рилс собирается по теме (диапазон индексов клипов = локация).
  - ХУК: самый зрелищный сегмент — ПЕРВЫМ. Дальше — нарастание к финалу внутри рилса.
  - Длина плана: по сетке (по умолчанию ровные ~3.5-4с, т.к. музыки ещё нет;
    появится трек — пересоберём по музыкальным фразам).
  - Мин. длина плана 2.5с (правило проекта). Общая длина 15-40с.

Выход на каждый рилс:
  output/reels/<name>.otio/.fcpxml/.edl   — таймлайн для Resolve
  output/reels/<name>_preview.mp4         — превью 540x960 (НЕ финал)
  печать состава с тайм-кодами

Исходники только читаются.
"""
import os, sys, json, subprocess
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import yaml
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otio_export import build_timeline, export_timeline

# порог визуального сходства: выше -> считаем кадры дублями одного сюжета
SIM_THRESHOLD = 0.75

# --- определения рилсов: тема -> какие клипы кандидаты -----------------------
REELS = [
    {
        "name": "reel01_ninhbinh_karst",
        "title": "Ниньбинь: карстовые пики и река",
        "candidate_idx": list(range(0, 30)),     # локация 1
        "target_len": 32,                        # целевая длина, сек
        "clip_len": 4.0,                         # длина плана (ровная сетка)
    },
    {
        "name": "reel02_halong_sunset",
        "title": "Халонг: закат, острова, лодки",
        "candidate_idx": list(range(30, 47)),    # локация 2 (47 — уже горы, исключён)
        "target_len": 32,
        "clip_len": 4.0,
    },
    {
        "name": "reel03_foggy_mountains",
        "title": "Туманные горы и водопады",
        "candidate_idx": list(range(48, 73)),    # локация 3 (idx63 битый, пропустится сам)
        "target_len": 32,
        "clip_len": 4.0,
    },
    {
        "name": "reel04_mucangchai_terraces",
        "title": "Мукангчай: рисовые террасы",
        "candidate_idx": list(range(73, 108)),   # локация 4, вся вертикаль без кропа
        "target_len": 32,
        "clip_len": 4.0,
    },
    # ===== ПАРТИЯ ГОНКОНГ (ночной город, фейерверки, закаты) =====
    # ОДИН рилс на партию (решение пользователя): история «закат -> неоновый
    # город -> фейерверк-финал». Хук — закатный скайлайн (idx31), кульминация —
    # салют (idx17-26). Дневные мутные мангры (14-16) исключены — не «вау».
    {
        "name": "reel_hk_night",
        "title": "Гонконг: закат, неон, фейерверк",
        "candidate_idx": [31, 32, 33, 27, 30] + list(range(34, 47))
                          + list(range(1, 14)) + list(range(17, 27)),
        "target_len": 34,
        "clip_len": 3.5,
    },
    {
        "name": "longform_hongkong",
        "title": "Гонконг — вертикальный фильм",
        "candidate_idx": list(range(0, 47)),
        "target_len": 180,
        "clip_len": 4.5,
    },
    {
        # ДЛИННЫЙ РОЛИК (вертикаль 9:16, 2-4 мин). Правило 7: спокойное начало,
        # нарастание, эффектное в ФИНАЛ (обратно рилсам). Все локации.
        "name": "longform_vietnam_vertical",
        "title": "Вьетнам — вертикальный фильм",
        "candidate_idx": list(range(0, 108)),
        "target_len": 180,
        "clip_len": 4.5,
    },
    {
        "name": "reel06_waterfalls",
        "title": "Водопады Севера",
        "candidate_idx": [66, 67, 68, 69, 70, 71, 72],
        "target_len": 34,
        "clip_len": 3.5,
    },
    {
        # отель Garrya Mù Cang Chải (подтверждено пользователем; GPS из SRT:
        # 21.805010, 104.143194, ~1172 м). Бамбуковые бунгало на сваях над
        # террасами. Тематически отдельная история от полей — не мешать.
        "name": "reel05_ecolodge",
        "title": "Garrya Mù Cang Chải",
        "candidate_idx": [96, 97, 98, 100, 101, 102, 103],
        "target_len": 30,
        "clip_len": 4.0,
    },
    # ===== ПАРТИЯ ТУРЦИЯ-АРАРАТ (вулкан, ветропарк, крепости) =====
    # ДЛИННЫЙ ФИЛЬМ 16:9 (решение пользователя: только длинный, рилсы/кроп НЕ делаем).
    # Правило 7: спокойное начало -> нарастание -> кульминация в ФИНАЛЕ.
    # Три акта по хронологии (= нарастание): вулкан-рассвет (idx 0-7) ->
    # ветропарк на плато (8-16) -> зелёные горы и каменные крепости (17-24, кульминация).
    # Открытие idx0 (восход), закрытие idx24 (вид сверху на лесную дорогу, score 0.96).
    # Вертикальные idx 2 и 15 ИСКЛЮЧЕНЫ (не 16:9). Итого 23 горизонтальных клипа.
    # ВАЖНО: перед запуском вернуть турецкий каталог в clip_catalog.json
    #   (Copy-Item work\clip_catalog_turkey.json work\clip_catalog.json -Force) —
    #   clip_catalog.json затирается каталогами других партий (см. память session-isolation).
    {
        "name": "longform_turkey_ararat",
        "title": "Turkey Ararat: volcano, wind farm, castles",
        "aspect": "16:9",      # ГОРИЗОНТАЛЬНЫЙ фильм: рендер 1920x1080, БЕЗ кропа
        "candidate_idx": [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16,
                          17, 18, 19, 20, 21, 22, 23, 24],
        "target_len": 120,     # запрос ~90-120с, но ЧЕСТНЫЙ потолок ~70с чистого
                               # материала (~9 различимых сцен); плотнее — повтор/слабые дубли.
        "clip_len": 8.0,       # ДЛИННЫЕ держащие планы (~4 такта) под медленный
                               # рост Woodkid; меньше склеек, кадр «проживается».
    },
    {
        # ВЕРТИКАЛЬНЫЙ рилс 9:16 из Турции — МИКС всех локаций (как длинный фильм),
        # только хорошие/hero кадры. БЕЗ поля aspect -> дефолт 9:16: горизонтали
        # кропаются с трекингом (правило 9), 2 нативные вертикали (idx2,15) — без кропа.
        # Хук первым (правило 8). Все idx кандидаты -> авто-отбор берёт лучшие по скору+дедуп.
        "name": "reel_turkey_mix",
        "title": "Турция: вулкан, ветропарк, крепости (вертикаль)",
        "candidate_idx": list(range(0, 25)),
        "target_len": 30,
        "clip_len": 3.0,
    },
]


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def signature(ffmpeg, path, t_center):
    """Визуальная подпись кадра: HS-гистограмма (цвет) + грубая яркостная карта
    (структура). Один кадр через ffmpeg из середины выбранного окна."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t_center:.2f}",
           "-i", path, "-frames:v", "1", "-vf", "scale=160:284",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30).stdout
        if len(out) < 160 * 284 * 3:
            return None
        img = np.frombuffer(out[:160 * 284 * 3], np.uint8).reshape(284, 160, 3)
    except Exception:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [12, 12], [0, 180, 0, 256])
    hist = cv2.normalize(hist, hist).flatten()           # цвет
    struct = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (14, 25)).flatten().astype(np.float32)
    struct = (struct - struct.mean()) / (struct.std() + 1e-6)  # структура (норм.)
    return {"color": hist, "struct": struct}


def similarity(a, b):
    """0..1: 0.55*цвет + 0.45*структура (корреляция)."""
    c = float(cv2.compareHist(a["color"].astype(np.float32),
                              b["color"].astype(np.float32), cv2.HISTCMP_CORREL))
    s = float(np.corrcoef(a["struct"], b["struct"])[0, 1])
    return max(0.0, 0.55 * max(0, c) + 0.45 * max(0, s))


def window_stats(catalog_clip, start, length):
    """Средние метрики по выбранному окну: динамика, цветность, экспозиция."""
    secs = [s for s in catalog_clip.get("seconds", [])
            if start <= s["t"] < start + length]
    if not secs:
        return {"color": 0, "flow": 0, "over": 0, "luma": 128}
    return {
        "color": float(np.mean([s["color"] for s in secs])),
        "flow": float(np.mean([s["flow"] for s in secs])),
        "over": float(np.mean([s["over_pct"] for s in secs])),
        "luma": float(np.mean([s["luma"] for s in secs])),
    }


def hook_score(ws):
    """Насколько кадр «цепляющий» в первые секунды: динамика + цвет + норм. экспозиция."""
    motion = min(1.0, ws["flow"] / 2.5)          # движение/облёт притягивает взгляд
    color = min(1.0, ws["color"] / 45.0)
    expo = 1.0 - min(0.7, ws["over"] / 8.0)      # штраф за выбитые света
    if ws["luma"] < 35:                          # слишком тёмный кадр — слабый хук
        expo -= 0.3
    return 0.45 * motion + 0.35 * color + 0.20 * max(0, expo)


def pick_segments(cfg, catalog, cand_idx, clip_len, target_len, min_clip=2.5):
    """Жадный отбор разнообразных кадров (дедуп по сюжету), затем хук + рост к финалу."""
    ffmpeg = cfg["tools"]["ffmpeg"]
    by_idx = {c["idx"]: c for c in catalog["clips"]}
    pool = [c for c in catalog["clips"]
            if c["idx"] in cand_idx and c["vertical"] and c.get("best_4s")]
    if not pool:
        return []

    key = "best_6s" if clip_len >= 5 else "best_4s"
    cand = []
    for c in pool:
        w = c.get(key) or c["best_4s"]
        if w is None:
            continue
        start = float(w["start"])
        cand.append({
            "idx": c["idx"], "path": c["path"], "file": c["file"],
            "start": start, "score": float(w["score"]), "dur": clip_len,
            "ws": window_stats(c, start, clip_len),
        })
    cand.sort(key=lambda x: -x["score"])

    n = max(2, int(round(target_len / clip_len)))

    # жадный отбор: берём по убыванию скора, пропускаем визуально похожие на уже взятые
    chosen, sigs = [], []
    for s in cand:
        if len(chosen) >= n:
            break
        sig = signature(ffmpeg, s["path"], s["start"] + clip_len / 2)
        if sig is None:
            continue
        if any(similarity(sig, ps) >= SIM_THRESHOLD for ps in sigs):
            continue                              # дубль сюжета — пропуск
        s["sig"] = sig
        chosen.append(s); sigs.append(sig)

    # если из-за дедупа набралось мало — добиваем наименее похожими из остатка
    if len(chosen) < min(n, 5):
        for s in cand:
            if len(chosen) >= n:
                break
            if any(s["idx"] == ch["idx"] for ch in chosen):
                continue
            sig = s.get("sig") or signature(ffmpeg, s["path"], s["start"] + clip_len / 2)
            if sig is None:
                continue
            chosen.append({**s, "sig": sig}); sigs.append(sig)

    if len(chosen) < 2:
        return []

    # ХУК = самый цепляющий (динамика/цвет/экспозиция), остальные по нарастанию скора
    for s in chosen:
        s["hook"] = hook_score(s["ws"])
    hook = max(chosen, key=lambda x: x["hook"])
    rest = [s for s in chosen if s is not hook]
    rest.sort(key=lambda x: x["score"])           # сильные ближе к финалу
    order = [hook] + rest

    segs = []
    for s in order:
        segs.append({
            "path": s["path"],
            "src_in": s["start"],
            "src_out": s["start"] + max(min_clip, s["dur"]),
            "name": f"[{s['idx']}] {s['file']} (score {s['score']:.2f}, hook {s['hook']:.2f})",
            "idx": s["idx"], "score": s["score"],
        })
    return segs


def render_preview(cfg, segs, out_mp4):
    """Низкоразрешённое превью 540x960 одним ffmpeg-прогоном (trim+concat)."""
    ffmpeg = cfg["tools"]["ffmpeg"]
    inputs, filters, labels = [], [], []
    for i, s in enumerate(segs):
        inputs += ["-ss", f"{s['src_in']:.3f}", "-t", f"{s['src_out']-s['src_in']:.3f}",
                   "-i", s["path"]]
        filters.append(f"[{i}:v]scale=540:960:force_original_aspect_ratio=decrease,"
                       f"pad=540:960:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]")
        labels.append(f"[v{i}]")
    fc = ";".join(filters) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=0[out]"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
        "-filter_complex", fc, "-map", "[out]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", out_mp4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ffmpeg ERROR:", (r.stderr or "")[-600:])
        return False
    return True


def main():
    cfg = load_cfg()
    cat_path = os.path.join(ROOT, "work", "clip_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    out_dir = os.path.join(ROOT, "output", "reels")
    os.makedirs(out_dir, exist_ok=True)

    for spec in REELS:
        print("=" * 70)
        print(f"РИЛС: {spec['title']}")
        segs = pick_segments(cfg, catalog, spec["candidate_idx"],
                             spec["clip_len"], spec["target_len"])
        if len(segs) < 2:
            print("  Недостаточно годных вертикальных клипов — пропуск."); continue

        total = sum(s["src_out"] - s["src_in"] for s in segs)
        print(f"  Планов: {len(segs)}, длина: {total:.1f}с")
        for j, s in enumerate(segs):
            tag = "ХУК " if j == 0 else f"{j+1:>4}"
            print(f"  {tag} {s['name']}  src {s['src_in']:.1f}-{s['src_out']:.1f}s")

        fps = 29.97
        tl = build_timeline(segs, name=spec["name"], fps=fps)
        base = os.path.join(out_dir, spec["name"])
        for w in export_timeline(tl, base):
            print(f"  таймлайн: {os.path.relpath(w, ROOT)}")

        prev = base + "_preview.mp4"
        print("  рендер превью (540x960)...")
        if render_preview(cfg, segs, prev):
            mb = os.path.getsize(prev) / 1e6
            print(f"  превью: {os.path.relpath(prev, ROOT)} ({mb:.1f} MB)")

    print("=" * 70)
    print("Готово. Превью — НЕ финал: без цветокора, низкое разрешение, ровная сетка")
    print("(музыки нет). Трек в music/ -> пересборка по музыкальным фразам.")


if __name__ == "__main__":
    main()
