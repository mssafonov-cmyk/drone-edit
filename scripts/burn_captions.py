# -*- coding: utf-8 -*-
"""
Прожиг текста в превью рилса: ТОЛЬКО заголовок локации в начале (курсив/цитата
в кадр больше не вжигаем — по решению пользователя цитата идёт отдельным текстом
к ролику, не в само видео). Работает поверх уже собранного превью
(stage4_reels_music.py), отдельным проходом ffmpeg — не трогает сборку/таймлайн.

Цитату каждый раз подбирает редактор (Claude) под смысл конкретного ролика —
базы цитат в проекте сознательно нет (так решили в разборе). Она пишется только
в <out>_caption.txt рядом с видео — вместе с тегами (для копипаста в подпись
поста; по гайдлайнам Instagram 2026 — 3-5 точных тегов, не общий список).

Использование:
    python burn_captions.py <in.mp4> <out.mp4> \
        --location "NINH BINH * VIETNAM" \
        --quote "He who climbs upon the highest mountains laughs at all tragedies, real or imaginary." \
        --author "NIETZSCHE" \
        --tags "ninhbinh,vietnamtravel,dronecinematography,karstlandscape,travelreels"
"""
import os, sys, subprocess, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = "C:/Windows/Fonts"
# Yu Gothic Light — тонкий, минималистичный, аутентично азиатский (не имитация
# "кисточкой" под Latin — это выглядит дёшево); хорошо держит разрядку букв.
FONT_LOCATION = f"{FONT_DIR}/YuGothL.ttc"


def _esc(path):
    """Экранирование для ffmpeg filtergraph (двоеточие диска, обратные слеши)."""
    return path.replace("\\", "/").replace(":", r"\:")


def _write_textfile(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _probe_height(cfg, path):
    ffprobe = os.path.join(os.path.dirname(cfg["tools"]["ffmpeg"]), "ffprobe.exe")
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=height", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return 1080


def burn_location(cfg, in_mp4, out_mp4, location, country="", loc_span=(0.0, 2.6), fade=0.3):
    ffmpeg = cfg["tools"]["ffmpeg"]
    # 4K-фильмы (2026-08-17): кегль/тень/битрейт масштабируются от высоты кадра,
    # иначе титул на 2160p вдвое мельче задуманного, а 16M мало для 4K.
    sc = _probe_height(cfg, in_mp4) / 1080.0
    tmp_dir = os.path.join(ROOT, "work", "tmp_captions")
    os.makedirs(tmp_dir, exist_ok=True)
    loc_file = os.path.join(tmp_dir, "loc.txt")
    _write_textfile(loc_file, location)

    def fade_expr(t0, t1, d):
        return (f"if(lt(t,{t0+d}),(t-{t0})/{d},"
                f"if(lt(t,{t1-d}),1,"
                f"if(lt(t,{t1}),({t1}-t)/{d},0)))")

    loc_alpha = fade_expr(*loc_span, fade)
    # 2026-08-09: «надпись почти незаметная с телефона» — крупнее (46->64), НИЖЕ и
    # несимметрично (y=28% высоты, не шапка), тень плотнее+размытее (читается на белом песке).
    draw_loc = (
        f"drawtext=fontfile='{_esc(FONT_LOCATION)}':textfile='{_esc(loc_file)}':"
        f"fontcolor=white:fontsize={int(64*sc)}:"
        f"x=(w-text_w)/2:y=h*0.28:"
        f"shadowcolor=black@0.85:shadowx={max(3,int(3*sc))}:shadowy={max(3,int(3*sc))}:"
        f"borderw=1:bordercolor=black@0.35:"
        f"enable='between(t,{loc_span[0]},{loc_span[1]})':alpha='{loc_alpha}'"
    )
    filters = [draw_loc]
    # Вторая строка — СТРАНА, отдельным центрированным drawtext чуть меньшим кеглем
    # под местом (лук локейшн-карточки: МЕСТО крупно, VIETNAM мельче). Отдельный
    # drawtext, чтобы каждая строка центрировалась сама (одним textfile многострочник
    # выравнивается по левому краю широкой строки — уезжает вбок).
    if country:
        cty_file = os.path.join(tmp_dir, "country.txt")
        _write_textfile(cty_file, country)
        draw_cty = (
            f"drawtext=fontfile='{_esc(FONT_LOCATION)}':textfile='{_esc(cty_file)}':"
            f"fontcolor=white:fontsize={int(38*sc)}:"
            f"x=(w-text_w)/2:y=h*0.28+{int(84*sc)}:"
            f"shadowcolor=black@0.85:shadowx={max(3,int(3*sc))}:shadowy={max(3,int(3*sc))}:borderw=1:bordercolor=black@0.35:"
            f"enable='between(t,{loc_span[0]},{loc_span[1]})':alpha='{loc_alpha}'"
        )
        filters.append(draw_cty)

    # МАСТЕРИНГ-проход (2026-08-10, «мыло после аплоада в IG»): это ПОСЛЕДНЕЕ перекодирование
    # видео перед выкладкой -> slow + crf16 + лёгкий шарпен (компенсация IG-пережатия) +
    # ЯВНЫЕ bt709-теги (без них IG washed) + faststart. Промежуточные шаги теперь crf14.
    filters.append("unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.4")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", in_mp4, "-vf", ",".join(filters),
           "-c:v", "libx264", "-preset", "slow", "-crf", "16",
           "-maxrate", ("40M" if sc > 1.3 else "16M"), "-bufsize", ("80M" if sc > 1.3 else "32M"),
           "-pix_fmt", "yuv420p",  # обяз.: телефоны не декодируют yuv444p (чёрный экран)
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-movflags", "+faststart",
           "-c:a", "copy", out_mp4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ffmpeg ERROR:", (r.stderr or "")[-1000:])
        return False
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("in_mp4")
    p.add_argument("out_mp4")
    p.add_argument("--location", required=True)
    p.add_argument("--country", default="", help="Вторая строка (страна) под местом, напр. 'V I E T N A M'")
    p.add_argument("--location-text", default="", help="Чистое название локации для подписей (без разрядки букв на кадре)")
    p.add_argument("--quote", default="")
    p.add_argument("--quote-ru", default="", help="Перевод цитаты для ВК (если не задан — берётся английский оригинал)")
    p.add_argument("--author", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--music", default="", help="Трек и старт фрагмента: 'Artist - Track @ m:ss' (IG глушит музыку — переналожить с этой секунды)")
    args = p.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = load_cfg()
    ok = burn_location(cfg, args.in_mp4, args.out_mp4, args.location, country=args.country)
    if not ok:
        return
    print(f"Готово: {os.path.relpath(args.out_mp4, ROOT)}")

    if args.quote or args.tags:
        cap_path = os.path.splitext(args.out_mp4)[0] + "_caption.txt"
        tags = [t.strip().lstrip("#") for t in args.tags.split(",") if t.strip()]
        hashtags = " ".join(f"#{t}" for t in tags[:5])
        quote_block = f"{args.quote}\n— {args.author}" if args.quote else ""
        quote_ru = args.quote_ru or args.quote
        loc = args.location_text or " ".join(args.location.split())

        with open(cap_path, "w", encoding="utf-8") as f:
            if getattr(args, "music", ""):
                f.write("МУЗЫКА (если IG заглушит — наложить с этой секунды): " + args.music + chr(10) + chr(10))
            f.write("=== INSTAGRAM REELS ===\n")
            f.write(f"{quote_block}\n\n{hashtags}\n\n")
            f.write("=== TIKTOK ===\n")
            f.write(f"{quote_block}\n\n{hashtags}\n\n")
            f.write("=== YOUTUBE (Shorts) ===\n")
            f.write(f"Title: {loc} | Cinematic Drone Reel\n")
            f.write(f"Description:\n{quote_block}\n\nFilmed by drone in {loc}.\n")
            f.write("Tags: " + ", ".join(tags) + "\n\n")
            f.write("=== VK (Клипы) ===\n")
            f.write(f"{loc}\n{quote_ru}" + (f"\n— {args.author}" if args.author else "") + "\n")
            if tags:
                f.write(" ".join(f"#{t}" for t in tags[:2]) + "\n")
        print(f"Подпись+теги (не в кадре): {os.path.relpath(cap_path, ROOT)}")


if __name__ == "__main__":
    main()
