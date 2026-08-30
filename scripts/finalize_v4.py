# -*- coding: utf-8 -*-
"""
Финализация v4-финалов Вьетнама (возврат правил #1,#2,#5 по списку 2026-08-08):
#1 прожиг локации (burn_captions, тексты — утверждённые из finalize_vietnam);
#2 сайдкар _caption.txt; #5 фильму — фейд 2.5с (видео+аудио, «оркестр закончил»).
Работает ПОВЕРХ готовых <name>_FINAL.mp4 (v4-грейд+звук): burn -> подмена FINAL.
Запуск: python finalize_v4.py [фильтр]
"""
import os, sys, subprocess
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "Vietnam")
PY = sys.executable
BURN = os.path.join(ROOT, "scripts", "burn_captions.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from finalize_vietnam import ITEMS   # утверждённые тексты/цитаты/теги

NAME2ITEM = {
    "reel01_ninhbinh": 0, "reel02_halong": 1, "reel03_sapa": 2,
    "reel04_mucangchai": 3, "reel05_garrya": 4, "reel06_waterfalls": 5,
    "longform_vietnam": 6,
}
FILM_FADE = 2.5


def dur_of(ffprobe, p):
    return float(subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", p], capture_output=True, text=True).stdout)


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    ffmpeg = cfg["tools"]["ffmpeg"]
    ffprobe = cfg["tools"].get("ffprobe") or ffmpeg.replace("ffmpeg", "ffprobe")
    flt = sys.argv[1] if len(sys.argv) > 1 else None
    for name, k in NAME2ITEM.items():
        if flt and flt not in name:
            continue
        it = ITEMS[k]
        fin = os.path.join(OUT, f"{name}_FINAL.mp4")
        if not os.path.exists(fin):
            print(f"ПРОПУСК {name}: нет FINAL"); continue
        src = fin
        # #5: фильму — фейд 2.5с (видео+аудио) отдельным проходом ДО прожига.
        # --no-fade: финал уходит в засвет солнца (v5) — затемнение не нужно.
        if name.startswith("longform") and "--no-fade" not in sys.argv:
            D = dur_of(ffprobe, fin)
            faded = os.path.join(OUT, f"_{name}_faded.mp4")
            st = max(0, D - FILM_FADE)
            r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", fin,
                                "-vf", f"fade=t=out:st={st:.2f}:d={FILM_FADE}",
                                "-af", f"afade=t=out:st={st:.2f}:d={FILM_FADE}",
                                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", faded],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"ERR fade {name}:", (r.stderr or "")[-300:]); continue
            src = faded
        # #1+#2: прожиг локации + сайдкар
        capped = os.path.join(OUT, f"_{name}_cap.mp4")
        cmd = [PY, BURN, src, capped, "--location", it["loc"], "--country", it["country"],
               "--location-text", it["loctext"], "--quote", it["quote"], "--author", it["author"],
               "--quote-ru", it["quote_ru"], "--tags", it["tags"]]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.exists(capped):
            print(f"ERR burn {name}:", (r.stderr or r.stdout or "")[-300:]); continue
        os.replace(capped, fin)                       # подмена FINAL (готовый v4+звук+титул)
        side = capped.replace(".mp4", "_caption.txt")  # сайдкар от burn — переименовать к FINAL
        if os.path.exists(side):
            os.replace(side, os.path.join(OUT, f"{name}_FINAL_caption.txt"))
        if src != fin and os.path.exists(src):
            os.remove(src)
        print(f"OK {name}: титул+сайдкар" + (" +фейд2.5" if name.startswith("longform") else ""))
    print("FINALIZE_V4 DONE")


if __name__ == "__main__":
    main()
