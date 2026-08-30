# -*- coding: utf-8 -*-
"""
РЕФЕРЕНС-НАРЕЗКА для калибровки вкуса. Берёт список клипов, у каждого выбирает
ЧИСТОЕ окно (доктрина+композиция), кропит по ориентации (вертикаль нативно,
горизонталь — трекинг по объекту), пробивает КРУПНЫЙ НОМЕР в кадр и склеивает в
ОДНО видео БЕЗ музыки. Пользователь называет номера хорошо/плохо + почему.

Пишет легенду <out>.legend.txt: номер -> idx, локация, окно, ориентация.
CLI: python reference_reel.py <out.mp4> <dur> "idx:loc,idx:loc,..."
"""
import os, sys, json, subprocess, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curate import clean_window_candidates
from vertical_crop import render_fixed_crop
import doctrine as DOC
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = "C\\:/Windows/Fonts/arialbd.ttf"


def pick_clean(ffmpeg, clip, dur, tmp):
    for st, _ in clean_window_candidates(clip, dur, 8):
        if [v for v in DOC.check_shot(clip, st, dur) if v["sev"] == "hard"]:
            continue
        fp = os.path.join(tmp, "_p.png")
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{st+dur/2:.2f}",
                        "-i", clip["path"], "-frames:v", "1", fp], capture_output=True)
        g = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
        if g is not None and DOC.composition_flags(g):
            continue
        return st, True
    return clean_window_candidates(clip, dur, 1)[0][0], False


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    out_mp4, dur = sys.argv[1], float(sys.argv[2])
    # токен: "idx:loc" (окно выбирает доктрина) ИЛИ "idx@start:loc" (форс-окно — показать
    # конкретный момент длинного клипа: мульти-сегмент «одно видео = много планов»)
    items = []
    for x in sys.argv[3].split(","):
        head, _, loc = x.partition(":")
        if "@" in head:
            a, b = head.split("@"); items.append((int(a), loc or "?", float(b)))
        else:
            items.append((int(head), loc or "?", None))
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    ffmpeg = cfg["tools"]["ffmpeg"]
    cat = json.load(open(os.path.join(ROOT, "work", "clip_catalog.json"), encoding="utf-8"))
    by = {c["idx"]: c for c in cat["clips"]}
    tmp = os.path.join(ROOT, "work", "refcut"); os.makedirs(tmp, exist_ok=True)
    parts, legend = [], []
    for n, (idx, loc, fstart) in enumerate(items, 1):
        clip = by[idx]
        if fstart is not None:
            win, clean = min(fstart, max(0.0, clip["duration_sec"] - dur - 0.1)), True
        else:
            win, clean = pick_clean(ffmpeg, clip, dur, tmp)
        seg = os.path.join(tmp, f"n{n:02d}.mp4")
        # номер (крупно, верх-центр) + мелкая подпись idx/локация внизу
        draw = (f"drawtext=fontfile='{FONT}':text='{n}':x=(w-tw)/2:y=40:fontsize=140:"
                f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=18,"
                f"drawtext=fontfile='{FONT}':text='idx{idx} @{win:.0f}s {loc}':x=20:y=h-70:fontsize=40:"
                f"fontcolor=yellow:box=1:boxcolor=black@0.5:boxborderw=8")
        if clip["vertical"]:
            src, ss = clip["path"], f"{win:.3f}"
        else:
            raw = os.path.join(tmp, f"raw{n:02d}.mp4")
            render_fixed_crop(cfg, clip["path"], win, dur + 0.2, 1080, 1920, raw)
            src, ss = raw, "0"
        vf = (f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              f"fps=30000/1001,{draw}")
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", ss, "-t", f"{dur:.3f}",
                        "-i", src, "-vf", vf, "-an", "-c:v", "libx264", "-preset", "fast",
                        "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30000/1001", seg], capture_output=True)
        parts.append(seg)
        legend.append(f"{n:2d}: idx{idx} {loc} окно {win:.1f}с {'ВЕРТ' if clip['vertical'] else 'ГОР-трек'} {'' if clean else '(чистого окна НЕТ — least-bad)'}")
        print(f"  {n:2d} idx{idx} {loc} {'v' if clip['vertical'] else 'h'} окно {win:.1f}с")
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.replace(chr(92),'/')}'\n")
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-an", out_mp4], capture_output=True)
    with open(out_mp4 + ".legend.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(legend))
    print(f"Готово: {os.path.relpath(out_mp4, ROOT)}  ({len(parts)} кусков по {dur}с)")
    print("Легенда:", out_mp4 + ".legend.txt")


if __name__ == "__main__":
    main()
