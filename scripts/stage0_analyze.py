# -*- coding: utf-8 -*-
"""
Этап 0 — Калибровка стиля.

Анализирует:
  - references/     эталонные ролики -> ритм монтажа (длины планов) + плавность камеры
  - color_targets/  эталонные кадры/видео -> целевой цветовой профиль

Результат: work/style_profile.json  — целевой «вкус» проекта для следующих этапов.

Исходники только читаются. Запуск:
    & <python> scripts\\stage0_analyze.py
"""

import os
import sys
import json
import glob
import subprocess
import tempfile

# Принудительно UTF-8 для вывода (Windows-консоль по умолчанию cp1251)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import yaml
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def log(msg):
    print(msg, flush=True)


def imread_u(path):
    """cv2.imread с поддержкой не-ASCII путей (Windows)."""
    try:
        return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_files(folder, exts):
    if not os.path.isdir(folder):
        return []
    out = []
    for p in sorted(glob.glob(os.path.join(folder, "*"))):
        if p.lower().endswith(exts):
            out.append(p)
    return out


# ---------------------------------------------------------------------------
#  РИТМ МОНТАЖА: детекция смен сцен -> распределение длин планов
# ---------------------------------------------------------------------------
def analyze_rhythm(path, cfg):
    from scenedetect import detect, ContentDetector
    sd = cfg["stage0"]["scene_detect"]
    try:
        scenes = detect(
            path,
            ContentDetector(
                threshold=sd["threshold"],
                min_scene_len=int(sd["min_scene_len_sec"] * 24),  # в кадрах (≈24fps)
            ),
        )
    except Exception as e:
        return {"error": f"scenedetect: {e}"}

    durations = [(end.seconds - start.seconds) for start, end in scenes]
    durations = [d for d in durations if d > 0]
    if not durations:
        return {"cuts": 0, "note": "сцены не выделены (возможно один непрерывный план)"}

    arr = np.array(durations)
    return {
        "cuts": len(durations),
        "mean_shot_sec": round(float(arr.mean()), 2),
        "median_shot_sec": round(float(np.median(arr)), 2),
        "min_shot_sec": round(float(arr.min()), 2),
        "max_shot_sec": round(float(arr.max()), 2),
        "std_shot_sec": round(float(arr.std()), 2),
        # доля коротких планов (< 2.5с) — индикатор «клиповости»
        "pct_under_2_5s": round(float((arr < 2.5).mean() * 100), 1),
    }


# ---------------------------------------------------------------------------
#  ПЛАВНОСТЬ КАМЕРЫ: средняя величина оптического потока + его разброс
# ---------------------------------------------------------------------------
def analyze_motion(path, cfg):
    m = cfg["stage0"]["motion"]
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"error": "cv2 не открыл файл"}

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / m["sample_fps"])))
    max_frames = int(m["max_seconds"] * src_fps)

    prev = None
    mags = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok or idx > max_frames:
            break
        if idx % step == 0:
            w = m["downscale_width"]
            h = int(frame.shape[0] * w / frame.shape[1])
            small = cv2.cvtColor(cv2.resize(frame, (w, h)), cv2.COLOR_BGR2GRAY)
            if prev is not None:
                flow = cv2.calcOpticalFlowFarneback(
                    prev, small, None, 0.5, 3, 15, 3, 5, 1.2, 0
                )
                mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
                mags.append(float(mag.mean()))
            prev = small
        idx += 1
    cap.release()

    if not mags:
        return {"error": "недостаточно кадров для анализа потока"}

    arr = np.array(mags)
    mean = float(arr.mean())
    std = float(arr.std())
    # эвристика «характера» камеры
    if mean < 1.5:
        character = "спокойная/статичная"
    elif mean < 4.0:
        character = "плавное движение"
    else:
        character = "динамичная"
    return {
        "motion_mean": round(mean, 3),       # средняя интенсивность движения в кадре
        "motion_std": round(std, 3),         # разброс = рывки/нестабильность
        "smoothness": round(1.0 / (1.0 + std), 3),  # 0..1, выше = плавнее
        "character": character,
    }


# ---------------------------------------------------------------------------
#  ЦВЕТ: профиль по кадрам (тени/света, насыщенность, контраст, теплота)
# ---------------------------------------------------------------------------
def color_stats_from_bgr(img, cfg):
    c = cfg["stage0"]["color"]
    mx = c["sample_max_px"]
    h, w = img.shape[:2]
    if max(h, w) > mx:
        scale = mx / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))

    bgr = img.astype(np.float32)
    b, g, r = bgr[..., 0], bgr[..., 1], bgr[..., 2]

    # яркость (luma) и перцентили теней/светов
    luma = 0.114 * b + 0.587 * g + 0.299 * r
    shadows = float(np.percentile(luma, c["shadows_pct"]))
    highlights = float(np.percentile(luma, c["highlights_pct"]))
    contrast = float(luma.std())

    # насыщенность через HSV
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    saturation = float(hsv[..., 1].mean())

    # теплота: баланс красного к синему (>0 теплее, <0 холоднее)
    warmth = float((r.mean() - b.mean()))

    return {
        "shadows": shadows,
        "highlights": highlights,
        "contrast": contrast,
        "saturation": saturation,
        "warmth": warmth,
        "mean_rgb": [round(float(r.mean()), 1),
                     round(float(g.mean()), 1),
                     round(float(b.mean()), 1)],
    }


def sample_video_frames(path, cfg, n=8):
    """Достаём n равномерных кадров из видео для анализа цвета."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    frames = []
    if total <= 0:
        cap.release()
        return frames
    for i in range(n):
        pos = int(total * (i + 0.5) / n)
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ok, fr = cap.read()
        if ok:
            frames.append(fr)
    cap.release()
    return frames


def analyze_color_targets(files, cfg):
    per_sample = []
    for p in files:
        if p.lower().endswith(IMAGE_EXT):
            img = imread_u(p)
            if img is not None:
                s = color_stats_from_bgr(img, cfg)
                s["_src"] = os.path.basename(p)
                per_sample.append(s)
        elif p.lower().endswith(VIDEO_EXT):
            for fr in sample_video_frames(p, cfg):
                s = color_stats_from_bgr(fr, cfg)
                s["_src"] = os.path.basename(p)
                per_sample.append(s)

    if not per_sample:
        return None

    def avg(key):
        return round(float(np.mean([s[key] for s in per_sample])), 1)

    profile = {
        "samples_analyzed": len(per_sample),
        "shadows": avg("shadows"),          # целевой уровень теней (0..255)
        "highlights": avg("highlights"),    # целевой уровень светов (0..255)
        "contrast": avg("contrast"),        # целевой контраст (std яркости)
        "saturation": avg("saturation"),    # целевая насыщенность (0..255)
        "warmth": avg("warmth"),            # целевая теплота (R-B)
        "mean_rgb": [
            round(float(np.mean([s["mean_rgb"][0] for s in per_sample])), 1),
            round(float(np.mean([s["mean_rgb"][1] for s in per_sample])), 1),
            round(float(np.mean([s["mean_rgb"][2] for s in per_sample])), 1),
        ],
    }
    # человекочитаемая интерпретация
    notes = []
    notes.append("тёплый" if profile["warmth"] > 8 else
                 "холодный" if profile["warmth"] < -8 else "нейтральный по температуре")
    notes.append("насыщенный" if profile["saturation"] > 110 else
                 "приглушённый" if profile["saturation"] < 70 else "средняя насыщенность")
    notes.append("контрастный" if profile["contrast"] > 60 else
                 "мягкий контраст" if profile["contrast"] < 40 else "средний контраст")
    profile["interpretation"] = ", ".join(notes)
    return profile


# ---------------------------------------------------------------------------
#  MAIN
# ---------------------------------------------------------------------------
def main():
    cfg = load_config()
    work = os.path.join(ROOT, cfg["paths"]["work"])
    os.makedirs(work, exist_ok=True)

    ref_dir = os.path.join(ROOT, cfg["paths"]["references"])
    col_dir = os.path.join(ROOT, cfg["paths"]["color_targets"])

    ref_files = list_files(ref_dir, VIDEO_EXT)

    # color_targets может содержать подпапки (point_A_photo, target_drone) или плоские файлы
    col_subdirs = []
    if os.path.isdir(col_dir):
        col_subdirs = [d for d in sorted(glob.glob(os.path.join(col_dir, "*")))
                       if os.path.isdir(d)]
    col_files_flat = list_files(col_dir, VIDEO_EXT + IMAGE_EXT)

    log("=" * 70)
    log("ЭТАП 0 — Калибровка стиля")
    log("=" * 70)
    log(f"Референс-видео найдено:   {len(ref_files)}")
    if col_subdirs:
        log(f"Папок цветовых таргетов:   {len(col_subdirs)} "
            f"({', '.join(os.path.basename(d) for d in col_subdirs)})")
    else:
        log(f"Цветовых таргетов найдено: {len(col_files_flat)}")
    log("")

    profile = {
        "stage": 0,
        "references": {"count": len(ref_files), "items": []},
        "color_profiles": {},        # профиль по каждой подпапке color_targets
        "color_target_active": None, # какой профиль ведёт пайплайн (целевой дрон-лук)
        "color_target": None,        # совместимость: профиль активной цели
        "warnings": [],
    }

    # --- ритм + плавность по каждому референсу ---
    rhythm_means, smoothness_vals = [], []
    for p in ref_files:
        name = os.path.basename(p)
        log(f"[референс] {name}")
        rhythm = analyze_rhythm(p, cfg)
        log(f"   ритм:     {rhythm}")
        motion = analyze_motion(p, cfg)
        log(f"   движение: {motion}")
        profile["references"]["items"].append(
            {"file": name, "rhythm": rhythm, "motion": motion}
        )
        if "mean_shot_sec" in rhythm:
            rhythm_means.append(rhythm["mean_shot_sec"])
        if "smoothness" in motion:
            smoothness_vals.append(motion["smoothness"])
        log("")

    if rhythm_means:
        profile["references"]["target_mean_shot_sec"] = round(float(np.mean(rhythm_means)), 2)
    if smoothness_vals:
        profile["references"]["target_smoothness"] = round(float(np.mean(smoothness_vals)), 3)

    # --- цветовые профили (по подпапкам или плоско) ---
    # какая подпапка — активная цель (ведёт пайплайн)
    target_dir_cfg = cfg.get("color_grade", {}).get("target_dir", "")
    target_name = os.path.basename(target_dir_cfg.rstrip("/\\")) if target_dir_cfg else None

    if col_subdirs:
        for sub in col_subdirs:
            name = os.path.basename(sub)
            files = list_files(sub, VIDEO_EXT + IMAGE_EXT)
            if not files:
                continue
            log(f"[цвет] анализ '{name}' ({len(files)} файлов)...")
            prof = analyze_color_targets(files, cfg)
            profile["color_profiles"][name] = prof
            log(f"   профиль: {prof}")
        log("")
        if target_name and target_name in profile["color_profiles"]:
            profile["color_target_active"] = target_name
            profile["color_target"] = profile["color_profiles"][target_name]
        else:
            profile["warnings"].append(
                f"target_dir='{target_name}' не найден среди подпапок color_targets — "
                f"целевой профиль не выбран."
            )
    elif col_files_flat:
        log("[цвет] анализ цветовых таргетов (плоско)...")
        prof = analyze_color_targets(col_files_flat, cfg)
        profile["color_profiles"]["flat"] = prof
        profile["color_target"] = prof
        profile["color_target_active"] = "flat"
        log(f"   профиль: {prof}")
        log("")

    # --- предупреждения о пустых входах ---
    if not ref_files:
        profile["warnings"].append(
            "references/ пуста — целевой ритм и плавность монтажа не заданы. "
            "Положи 1-3 эталонных ролика и перезапусти этап 0."
        )
    if not col_subdirs and not col_files_flat:
        profile["warnings"].append(
            "color_targets/ пуста — целевой цветовой профиль не задан."
        )

    out_path = os.path.join(work, "style_profile.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    log("=" * 70)
    log(f"Готово -> {os.path.relpath(out_path, ROOT)}")
    for w in profile["warnings"]:
        log(f"  ВНИМАНИЕ: {w}")
    log("=" * 70)


if __name__ == "__main__":
    main()
