# -*- coding: utf-8 -*-
"""
Нарезка кандидатов главы в пронумерованные превью для РУЧНОГО отбора
пользователем: он смотрит папку/контактный лист и называет номера.

Отбирает ПЛАВНЫЕ окна (без рывков и старт-стопов) скользящим окном внутри
сегментов — из одного длинного дубля берётся несколько разнесённых кусков
(правило проекта). Пишет:
  output/review_<name>/NN_<key>_<file>.mp4  — клипы по одному
  output/review_<name>/CONTACT.jpg          — контактный лист с номерами
  output/review_<name>/list.txt             — расшифровка номер -> ключ/таймкод

Использование:
  python make_review_clips.py <name> <idx_from> <idx_to> [win_sec]
Пример:
  python make_review_clips.py reel4 73 107 3
"""
import os, sys, json, subprocess
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JERK_MAX = 0.55
FLOW_MIN = 0.8
RATIO_MAX = 2.5
OVER_MAX = 3.0
MAX_PER_CLIP = 2
MIN_GAP_SEC = 6


def load_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_smooth(catalog, idx_lo, idx_hi, win):
    good = []
    for c in catalog["clips"]:
        if not (idx_lo <= c["idx"] <= idx_hi) or not c["vertical"]:
            continue
        secs = c["seconds"]
        for si, seg in enumerate(c.get("segments", [])):
            lo, hi = int(seg["start"]), int(seg["end"])
            for s in range(lo, hi - win + 1):
                w = [x for x in secs if s <= x["t"] < s + win]
                if len(w) < win:
                    continue
                flow = np.array([x["flow"] for x in w])
                jerk = np.array([x["jerk"] for x in w])
                if jerk.max() > JERK_MAX or flow.mean() < FLOW_MIN:
                    continue
                if flow.max() / max(0.05, flow.min()) > RATIO_MAX:
                    continue
                if np.mean([x["over_pct"] for x in w]) > OVER_MAX:
                    continue
                sharp = np.mean([x["sharp"] for x in w])
                score = (1 - jerk.max() / JERK_MAX) * 0.5 + min(1, sharp / 300) * 0.5
                good.append({"key": f"{c['idx']}.{si}", "idx": c["idx"], "file": c["file"],
                             "path": c["path"], "t": s, "flow": float(flow.mean()),
                             "jerk": float(jerk.max()), "score": float(score)})
    good.sort(key=lambda g: -g["score"])
    picked, per_clip = [], {}
    for g in good:
        lst = per_clip.setdefault(g["idx"], [])
        if len(lst) >= MAX_PER_CLIP or any(abs(g["t"] - t) < MIN_GAP_SEC for t in lst):
            continue
        lst.append(g["t"]); picked.append(g)
    picked.sort(key=lambda g: (g["idx"], g["t"]))
    return picked


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 4:
        print(__doc__); return
    name, idx_lo, idx_hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    win = int(sys.argv[4]) if len(sys.argv) > 4 else 3

    cfg = load_cfg()
    ffmpeg = cfg["tools"]["ffmpeg"]
    with open(os.path.join(ROOT, "work", "clip_catalog.json"), "r", encoding="utf-8") as f:
        catalog = json.load(f)

    picked = find_smooth(catalog, idx_lo, idx_hi, win)
    out_dir = os.path.join(ROOT, "output", f"review_{name}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"Плавных кандидатов: {len(picked)} -> {os.path.relpath(out_dir, ROOT)}")

    thumbs, lines = [], []
    for n, g in enumerate(picked, 1):
        base = f"{n:02d}_{g['key'].replace('.', '_')}_{os.path.splitext(g['file'])[0]}"
        mp4 = os.path.join(out_dir, base + ".mp4")
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{g['t']}", "-t", f"{win}", "-i", g["path"],
                        "-vf", "scale=540:960,fps=29.97", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "23", "-an", mp4],
                       capture_output=True)
        # кадр с крупным номером для контактного листа
        jpg = os.path.join(out_dir, f".t{n:02d}.jpg")
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{g['t'] + win/2}", "-i", g["path"], "-frames:v", "1",
                        "-vf", (f"scale=150:267,drawtext=text='{n}':fontcolor=white:fontsize=44:"
                                "box=1:boxcolor=black@0.6:boxborderw=6:x=6:y=6"), jpg],
                       capture_output=True)
        thumbs.append(jpg)
        lines.append(f"{n:02d}  {g['key']:8s} {g['file']:16s} t={g['t']:3d}-{g['t']+win:3d}с  "
                     f"движение {g['flow']:5.2f}  рывок {g['jerk']:.2f}")

    # контактные листы по 12 в ряд
    rows, per_row = [], 12
    for i in range(0, len(thumbs), per_row):
        chunk = thumbs[i:i + per_row]
        row = os.path.join(out_dir, f".row{i}.jpg")
        inputs = []
        for t in chunk:
            inputs += ["-i", t]
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + inputs +
                       ["-filter_complex", f"hstack=inputs={len(chunk)}", row],
                       capture_output=True)
        rows.append(row)
    if rows:
        inputs = []
        for r in rows:
            inputs += ["-i", r]
        contact = os.path.join(out_dir, "CONTACT.jpg")
        if len(rows) == 1:
            subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                            "-i", rows[0], "-c", "copy", contact], capture_output=True)
        else:
            subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + inputs +
                           ["-filter_complex",
                            f"[0:v]pad=iw+0:ih[a];" if False else f"vstack=inputs={len(rows)}",
                            contact], capture_output=True)
        print(f"Контактный лист: {os.path.relpath(contact, ROOT)}")

    with open(os.path.join(out_dir, "list.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    for t in thumbs + rows:
        try:
            os.remove(t)
        except OSError:
            pass
    print("\n".join(lines))


if __name__ == "__main__":
    main()
