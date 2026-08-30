# -*- coding: utf-8 -*-
"""
Этап 5 — Цветокор (превью вариантов).

Применяет варианты грейда к готовому превью рилса (или к одному кадру для
сравнения). По CLAUDE.md: фото-стиль (точка А) и дрон-лук — РАЗНЫЕ; ведёт
пайплайн target_drone (work/color_target_spec.md). Финальный рендер — только
после явного выбора варианта пользователем (жёсткое правило 2).

3 варианта (ffmpeg colorbalance/eq/curves — без внешних LUT, всё воспроизводимо):
  A "cinematic"  — целевой дрон-лук: orange-teal сплит-тон, насыщенность −15%,
                   защищённые света, filmic toe. Это «куда идём» (target_drone).
  B "warm"       — тёплый золотой, наследие фото (точка А @maximsafonov: тепло
                   +18, насыщенность 115). Для золотого часа/заката.
  C "cool"       — холодный киношный muted: глубокий тил в тенях, насыщенность
                   −25%, выше контраст. Настроенческий/драматичный.

Использование:
  # сравнение на одном кадре (быстро, для калибровки):
  python stage5_colorgrade.py frame <in.mp4> <t_sec> <out_compare.jpg>
  # применить все варианты к видео:
  python stage5_colorgrade.py video <in.mp4> <out_prefix>   -> <prefix>__gradeA.mp4 и т.д.
"""
import os, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# --- фильтр-чейны вариантов ------------------------------------------------
# colorbalance: rs/gs/bs (тени), rm/gm/bm (миды), rh/gh/bh (света), −1..1.
#   +bs = синий в тенях, +gs = зелёный -> тил. +rh = красный в светах,
#   −bh = меньше синего в светах -> тёплый янтарь. Держим миды ~нейтрально.
# curves: filmic toe — приподнять чёрный (тени не в 0), мягкий rolloff светов.
GRADES = {
    "A_cinematic": (
        "colorbalance=rs=-0.04:gs=0.03:bs=0.08:rm=0.01:bm=-0.01:rh=0.06:gh=0.02:bh=-0.06,"
        "curves=r='0/0.02 0.25/0.24 0.75/0.78 1/0.97':"
        "b='0/0.04 0.5/0.49 1/0.95',"
        "eq=contrast=1.06:saturation=0.85:gamma=1.0"
    ),
    "B_warm": (
        "colorbalance=rs=0.03:gs=0.01:bs=-0.03:rh=0.07:gh=0.03:bh=-0.08,"
        "colortemperature=temperature=5200,"
        "curves=all='0/0.03 0.5/0.53 1/0.99',"
        "eq=contrast=1.04:saturation=1.06:gamma=1.02"
    ),
    "C_cool": (
        "colorbalance=rs=-0.07:gs=0.04:bs=0.12:rm=-0.02:bm=0.02:rh=0.03:gh=0.01:bh=-0.03,"
        "curves=r='0/0.03 0.25/0.22 1/0.94':b='0/0.06 0.5/0.52 1/0.97',"
        "eq=contrast=1.12:saturation=0.75:gamma=0.98"
    ),
}


def grade_frame_compare(cfg, in_mp4, t_sec, out_jpg):
    """Один кадр -> оригинал + 3 грейда рядом (для калибровки)."""
    ffmpeg = cfg["tools"]["ffmpeg"]
    tmp = os.path.join(ROOT, "work", "tmp_grade")
    os.makedirs(tmp, exist_ok=True)
    variants = [("orig", None)] + list(GRADES.items())
    frames = []
    for name, chain in variants:
        fp = os.path.join(tmp, f"cmp_{name}.jpg")
        vf = "scale=270:480" if chain is None else f"{chain},scale=270:480"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", f"{t_sec}", "-i", in_mp4, "-frames:v", "1", "-vf", vf, fp]
        subprocess.run(cmd, capture_output=True)
        frames.append(fp)
    inputs = []
    for fp in frames:
        inputs += ["-i", fp]
    cmd = ([ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + inputs +
           ["-filter_complex", f"hstack=inputs={len(frames)}", out_jpg])
    subprocess.run(cmd, capture_output=True)
    return out_jpg


def grade_video(cfg, in_mp4, out_prefix):
    """Применить все варианты грейда к видео. Аудио копируется."""
    ffmpeg = cfg["tools"]["ffmpeg"]
    outs = []
    for name, chain in GRADES.items():
        out = f"{out_prefix}__grade{name[0]}.mp4"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-i", in_mp4, "-vf", chain,
               "-c:v", "libx264", "-preset", "fast", "-crf", "18",
               "-pix_fmt", "yuv420p",  # обяз.: иначе x264 выдаёт yuv444p — телефоны не декодируют (чёрный экран)
               "-c:a", "copy", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ОШИБКА грейда {name}:", (r.stderr or "")[-400:])
            continue
        outs.append(out)
        print(f"  {name}: {os.path.relpath(out, ROOT)}")
    return outs


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_cfg()
    if len(sys.argv) < 2:
        print(__doc__); return
    mode = sys.argv[1]
    if mode == "frame":
        _, _, in_mp4, t_sec, out_jpg = sys.argv
        grade_frame_compare(cfg, in_mp4, float(t_sec), out_jpg)
        print(f"Сравнение кадра: {os.path.relpath(out_jpg, ROOT)}")
    elif mode == "video":
        _, _, in_mp4, out_prefix = sys.argv
        grade_video(cfg, in_mp4, out_prefix)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
