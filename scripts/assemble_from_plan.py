# -*- coding: utf-8 -*-
"""
Сборка рилса по cut_plan.json (точки монтажа из внешнего бит-анализа Beat This!).
- 8 планов, точные ДЛИТЕЛЬНОСТИ В КАДРАХ из cut_plan (frame-accurate, не суммируем).
- Для каждого слота выбираем ОКНО исходника, где движение усиливается к концу (к акценту) —
  правило 10 пользователя (не просто попасть в сетку).
- Рендер 9:16 (1080x1920, crop-to-fill для горизонталей), concat, мукс того же WAV
  (фрагмент от start_time плана).

Использование: python assemble_from_plan.py <out.mp4> "idx,idx,..." (8 индексов на слоты)
"""
import os, sys, json, subprocess
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curate import clean_window, clean_window_candidates
from vertical_crop import render_fixed_crop   # ФИКС-кроп 16:9->9:16 (без дрожи трекинга)
import doctrine as DOC


def pick_composed_window(ffmpeg, clip, dur, tmp, slot):
    """Из топ-кандидатов берём ПЕРВЫЙ, что проходит движение (доктрина) И композицию
    (декодируем кадр). Иначе — лучший по скору (least-bad) + вернём его нарушения."""
    import cv2
    cands = clean_window_candidates(clip, dur, 8)
    best = cands[0][0]
    for st, _ in cands:
        hard = [v for v in DOC.check_shot(clip, st, dur, is_open=(slot == 1)) if v["sev"] == "hard"]
        if hard:
            continue
        fp = os.path.join(tmp, "_probe.png")
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{st + dur/2:.2f}", "-i", clip["path"], "-frames:v", "1", fp],
                       capture_output=True)
        g = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
        if g is not None and DOC.composition_flags(g):
            continue
        return st, True
    return best, False
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AD = os.environ.get("MUSIC_AD") or os.path.join(ROOT, "work", "music_analysis")


def load_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def pick_window(clip, dur_sec):
    """Окно длиной dur_sec: чистое движение + УСИЛЕНИЕ потока к концу (движение на акцент)."""
    secs = clip.get("seconds") or []
    cd = clip.get("duration_sec", dur_sec)
    L = int(round(dur_sec))
    hi = int(cd)
    if not secs or hi <= L:
        return max(0.0, (cd - dur_sec) / 2)
    by = {s["t"]: s for s in secs}
    best_off, best_val = 0, -1e9
    for off in range(0, hi - L + 1):
        win = [by.get(off + k) for k in range(L) if by.get(off + k)]
        if len(win) < max(1, L - 1):
            continue
        flow = [w["flow"] for w in win]
        jerk_max = max(w["jerk"] for w in win)
        curl_max = max(w.get("curl", 0) for w in win)
        head = flow[0]
        tail = np.mean(flow[-2:]) if len(flow) >= 2 else flow[-1]
        val = (np.mean(flow) * 0.5           # общее движение
               + tail * 0.6                   # СИЛЬНОЕ движение к концу (к акценту)
               - max(0, 0.5 - head) * 1.0      # не стартовать со стойки
               - min(1.2, jerk_max / 1.2) * 0.8   # без рывков
               - min(1.0, curl_max / 0.9) * 0.6)  # без орбит/крена
        if val > best_val:
            best_val, best_off = val, off
    return float(best_off)


def main():
    cfg = load_cfg(); ffmpeg = cfg["tools"]["ffmpeg"]
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
    out_mp4 = sys.argv[1]
    # слоты: "idx" (окно по доктрине) ИЛИ "idx:start" (форс-окно для контрового/солнца)
    idxs, forced = [], {}
    for i, tok in enumerate(sys.argv[2].split(",")):
        tok = tok.strip()
        if ":" in tok:
            a, b = tok.split(":"); idxs.append(int(a)); forced[i] = float(b)
        else:
            idxs.append(int(tok))
    plan = json.load(open(os.path.join(AD, "cut_plan.json"), encoding="utf-8"))
    shots = plan["shots"]; fps = 30000 / 1001
    cat_p = os.environ.get("CLIP_CATALOG") or os.path.join(ROOT, "work", "clip_catalog.json")
    cat = json.load(open(cat_p, encoding="utf-8"))
    by_idx = {c["idx"]: c for c in cat["clips"]}
    # --wav <путь|имя> — трек для мукса (по умолчанию ian_asher.wav). Тот же WAV, что анализировали.
    wav_arg = sys.argv[sys.argv.index("--wav") + 1] if "--wav" in sys.argv else "ian_asher.wav"
    wav = wav_arg if os.path.isabs(wav_arg) else os.path.join(AD, wav_arg)
    audio_start = plan["start_time"]

    tmp = os.path.join(ROOT, "work", "plan_shots", os.path.splitext(os.path.basename(out_mp4))[0])
    os.makedirs(tmp, exist_ok=True)
    parts = []; shots_meta = []
    print("слот  idx  файл            кадров  окно src")
    for i, s in enumerate(shots):
        idx = idxs[i]; clip = by_idx[idx]
        nf = s["dur_frames"]; dur = nf / fps
        clean = True
        if i in forced:                    # форс-окно (контровой/солнце — обходит детектор)
            win = min(forced[i], max(0.0, clip["duration_sec"] - dur - 0.1))
        else:
            win, clean = pick_composed_window(ffmpeg, clip, dur, tmp, i + 1)  # движение + композиция
        shots_meta.append({"slot": i + 1, "idx": idx, "win_start": round(win, 3),
                           "dur_sec": round(dur, 3), "dur_frames": nf, "forced": i in forced,
                           "clean": bool(clean or i in forced)})
        outp = os.path.join(tmp, f"shot{i:02d}.mp4")
        wide = "--aspect169" in sys.argv
        if wide:
            # ФИЛЬМ 16:9: только нативные горизонтали (кропы мылят — правило 2026-08-09)
            src = clip["path"]; ss = f"{win:.3f}"; t = f"{dur + 0.3:.3f}"
        elif clip["vertical"]:
            # нативная вертикаль — без кропа объекта (только масштаб)
            src = clip["path"]; ss = f"{win:.3f}"; t = f"{dur + 0.3:.3f}"
        else:
            # ГОРИЗОНТАЛЬ: трекинг-кроп по объекту (не центр!) -> промежуточный вертикальный клип
            raw = os.path.join(tmp, f"raw{i:02d}.mp4")
            render_fixed_crop(cfg, clip["path"], win, dur + 0.3, 1080, 1920, raw)
            src = raw; ss = "0"; t = f"{dur + 0.3:.3f}"
        if "--aspect169" in sys.argv:
            # --uhd (2026-08-17): фильм смотрят на ТВ/ПК — исходники 4K, рендер 1080p
            # давал мыло на большом экране («совсем без качества»). 16:9 умеет 4K.
            geom = ("scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160"
                    if "--uhd" in sys.argv else
                    "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080")
        else:
            geom = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        # setsar/format/bt709 (2026-08-18): исходники разных партий несут РАЗНЫЕ цветовые
        # метаданные (клип 102 = явный bt709) -> конкат меняет параметры потока на лету,
        # trim-счётчик грейда СБРАСЫВАЕТСЯ на границе и хвостовые куски выходят ПУСТЫМИ.
        # Все куски приводим к одному профилю.
        vf = (f"{geom},setsar=1,format=yuv420p,"
              f"fps=30000/1001,tpad=stop_mode=clone:stop_duration=2,"
              f"trim=end_frame={nf},setpts=PTS-STARTPTS")
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", ss, "-t", t, "-i", src,
               "-vf", vf, "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
               "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
               "-color_trc", "bt709", "-r", "30000/1001", outp]
        # ПРОВЕРКА КУСКА (та же дата): пустой/короткий кусок молча обрезал фильм на 42с.
        for attempt in (1, 2):
            rr = subprocess.run(cmd, capture_output=True, text=True)
            pr = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=nb_frames", "-of",
                                 "default=nw=1:nk=1", outp], capture_output=True, text=True)
            try:
                got = int(pr.stdout.strip())
            except Exception:
                got = 0
            if rr.returncode == 0 and abs(got - nf) <= 2:
                break
            if attempt == 2:
                print(f"FATAL: слот {i+1} idx{idx}: кадров {got}, ожидалось {nf}; "
                      f"stderr: {(rr.stderr or '')[-400:]}")
                sys.exit(2)
        parts.append(outp)
        print(f"  {i+1}   {idx:3d}  {clip['file'][:14]}  {nf:4d}   {win:.1f}s")

    # concat + мукс WAV-фрагмента
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.replace(chr(92),'/')}'\n")
    total = shots[-1]["end_time"] - shots[0]["start_time"]
    # --end-fade N: длинное затухание для ФИЛЬМОВ (обрыв музыки на даунбите читается
    # как «всё оборвалось»); по умолчанию короткое подзатухание рилсов.
    if "--end-fade" in sys.argv:
        fade = float(sys.argv[sys.argv.index("--end-fade") + 1])
    else:
        fade = min(0.7, total * 0.05)                 # короткое мягкое подзатухание (не «затихание»)
    fst = max(0.0, total - fade)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", lst,
           "-ss", f"{audio_start:.3f}", "-t", f"{total:.3f}", "-i", wav,
           "-map", "0:v", "-map", "1:a",
           "-af", f"afade=t=out:st={fst:.2f}:d={fade:.2f}",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-shortest", out_mp4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("ffmpeg concat ERROR:", (r.stderr or "")[-800:]); return
    sc = os.path.splitext(out_mp4)[0] + ".shots.json"
    json.dump({"out": os.path.basename(out_mp4), "wav": wav_arg, "start_time": audio_start,
               "total": round(total, 3), "shots": shots_meta},
              open(sc, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Готово: {os.path.relpath(out_mp4, ROOT)}  ({total:.2f}с, аудио от {audio_start:.2f}с)")
    print(f"  план кадров -> {os.path.relpath(sc, ROOT)}")


if __name__ == "__main__":
    main()
