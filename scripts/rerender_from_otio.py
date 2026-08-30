# -*- coding: utf-8 -*-
"""
Перерендер ролика ИЗ ГОТОВОГО OTIO через ИСПРАВЛЕННУЮ функцию рендера
(frame-exact CFR, урок #29 [[reels-pipeline]]). Нужен, чтобы починить A/V-дрейф
в УЖЕ УТВЕРЖДЁННЫХ роликах, не меняя монтаж: OTIO хранит точные клипы/тайминги/
порядок и аудио. needs_crop берём из каталога (горизонтальные = кроп).

ОГРАНИЧЕНИЕ: OTIO не хранит speed/zoom. Для роликов со спидапом контент разойдётся
(тайминг склеек всё равно сохранится). Проверять покадрово против доставленного.

Запуск:
  python scripts/rerender_from_otio.py <in.otio> <out.mp4> [--end-fade 0.6]
"""
import os, sys, json
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import yaml
import opentimelineio as otio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from stage4_reels_music import render_preview_with_audio


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _url_to_path(url):
    p = url.replace("file://", "")
    # file://D:/... -> D:/...
    return p.replace("/", os.sep)


def main():
    if len(sys.argv) < 3:
        print("usage: rerender_from_otio.py <in.otio> <out.mp4> [--end-fade N]")
        sys.exit(1)
    in_otio, out_mp4 = sys.argv[1], sys.argv[2]
    end_fade = 0.6
    if "--end-fade" in sys.argv:
        end_fade = float(sys.argv[sys.argv.index("--end-fade") + 1])

    cfg = load_cfg()
    # каталог для needs_crop (по vertical). Берём текущий work/clip_catalog.json.
    cat = json.load(open(os.path.join(ROOT, "work", "clip_catalog.json"), encoding="utf-8"))
    vert_by_path = {c["path"]: c.get("vertical", True) for c in cat["clips"]}
    # и по basename на случай расхождения путей
    vert_by_name = {os.path.basename(c["path"]): c.get("vertical", True)
                    for c in cat["clips"]}

    tl = otio.adapters.read_from_file(in_otio)
    segs, audio = [], None
    for tr in tl.tracks:
        kind = str(tr.kind)
        if "ideo" in kind:
            for i, clip in enumerate(tr):
                sr = clip.source_range
                src_in = sr.start_time.to_seconds()
                dur = sr.duration.to_seconds()
                path = _url_to_path(clip.media_reference.target_url)
                vertical = vert_by_path.get(path)
                if vertical is None:
                    vertical = vert_by_name.get(os.path.basename(path), True)
                segs.append({
                    "key": f"r{i}", "path": path,
                    "src_in": src_in, "src_out": src_in + dur,
                    "needs_crop": not vertical,
                    "name": clip.name,
                })
        elif "udio" in kind:
            clip = list(tr)[0]
            sr = clip.source_range
            audio = {
                "path": _url_to_path(clip.media_reference.target_url),
                "src_in": sr.start_time.to_seconds(),
                "dur": sr.duration.to_seconds(),
            }

    if audio is None:
        print("ОШИБКА: в OTIO нет аудио-дорожки"); sys.exit(1)

    total_dur = sum(s["src_out"] - s["src_in"] for s in segs)
    print(f"Клипов: {len(segs)}, длина {total_dur:.1f}с, кроп-клипов "
          f"{sum(1 for s in segs if s['needs_crop'])}, аудио старт {audio['src_in']:.2f}с")
    ok = render_preview_with_audio(cfg, segs, audio["path"], audio["src_in"],
                                   total_dur, out_mp4, fade=end_fade)
    print("Готово:" if ok else "ОШИБКА рендера:", out_mp4)


if __name__ == "__main__":
    main()
