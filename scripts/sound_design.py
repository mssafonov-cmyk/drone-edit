# -*- coding: utf-8 -*-
"""
Нативный звук: ambient-подложка под музыку готового ролика.
Библиотека: work/sfx/<тег>/*.{wav,mp3} (теги: ocean, seagulls, wind, jungle, birds,
waterfall, city_night, marina). Файл берётся случайно-детерминированно (по имени ролика),
лупится на длину, фейды, кладётся ПОД музыку с постоянным уровнем (по умолчанию −16 дБ).

Если файлов в теге нет — synth-заглушки для прослушки механики (ocean/wind/waterfall
синтезируются шумом с фильтрами; это ВРЕМЕННО, до наполнения библиотеки).

CLI: python sound_design.py <in.mp4> <out.mp4> <тег[,тег2]> [--bed-db -16]
"""
import os, sys, subprocess, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX = os.path.join(ROOT, "work", "sfx")

# synth-рецепты (заглушки): шум -> фильтры -> медленная амплитудная волна
SYNTH = {
    "ocean":     "anoisesrc=color=brown:amplitude=0.7,lowpass=f=850,highpass=f=60,"
                 "tremolo=f=0.11:d=0.55,tremolo=f=0.17:d=0.3",
    "wind":      "anoisesrc=color=pink:amplitude=0.5,lowpass=f=500,highpass=f=40,"
                 "tremolo=f=0.1:d=0.55",
    "waterfall": "anoisesrc=color=white:amplitude=0.55,lowpass=f=3000,highpass=f=200",
    "city_night":"anoisesrc=color=brown:amplitude=0.35,lowpass=f=300,highpass=f=30,"
                 "tremolo=f=0.05:d=0.3",
}


def dur_of(ffprobe, p):
    return float(subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", p], capture_output=True, text=True).stdout)


def pick_file(tag, seed):
    d = os.path.join(SFX, tag)
    if not os.path.isdir(d):
        return None
    fs = sorted(f for f in os.listdir(d) if f.lower().endswith((".wav", ".mp3", ".flac", ".m4a")))
    if not fs:
        return None
    k = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(fs)
    return os.path.join(d, fs[k])


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    ffmpeg = cfg["tools"]["ffmpeg"]
    ffprobe = cfg["tools"].get("ffprobe") or ffmpeg.replace("ffmpeg", "ffprobe")
    src, out = sys.argv[1], sys.argv[2]
    tags = sys.argv[3].split(",")
    bed_db = float(sys.argv[sys.argv.index("--bed-db") + 1]) if "--bed-db" in sys.argv else -16.0
    # интро-акцент: первые intro-sec подложка громче (слышно «место» до того, как трек заберёт)
    intro_db = float(sys.argv[sys.argv.index("--intro-db") + 1]) if "--intro-db" in sys.argv else None
    intro_sec = float(sys.argv[sys.argv.index("--intro-sec") + 1]) if "--intro-sec" in sys.argv else 2.2
    # видео из другого файла (напр. FINAL с титулами), аудио-база из src
    vid_from = sys.argv[sys.argv.index("--video-from") + 1] if "--video-from" in sys.argv else None
    D = dur_of(ffprobe, src)

    # --envelope env.json: по-плановая громкость (дальность объекта) с лин. интерполяцией
    env_p = sys.argv[sys.argv.index("--envelope") + 1] if "--envelope" in sys.argv else None
    if env_p:
        import json as _j
        pts = _j.load(open(env_p, encoding="utf-8"))["points"]
        # g(t) = g0 + sum((g[i+1]-g[i]) * clip((t-t_i)/(t_{i+1}-t_i)))
        expr = f"({pts[0]['db']})"
        for i in range(len(pts) - 1):
            t0, t1 = pts[i]["t"], pts[i + 1]["t"]
            dg = pts[i + 1]["db"] - pts[i]["db"]
            if abs(dg) < 0.05 or t1 <= t0:
                continue
            expr += f"+({dg})*min(max((t-{t0})/({t1}-{t0}),0),1)"
        vol = f"volume='pow(10,({expr})/20)':eval=frame"
        if intro_db is not None:   # интро-акцент поверх: max(огибающая, intro в первые сек)
            vol = (f"volume='pow(10,(max({expr},({intro_db})+({-60}-({intro_db}))*"
                   f"min(max((t-{intro_sec})/1.5,0),1)))/20)':eval=frame")
    elif intro_db is not None:
        vol = (f"volume='pow(10,({intro_db}+({bed_db}-({intro_db}))*"
               f"min(max((t-{intro_sec})/1.5,0),1))/20)':eval=frame")
    else:
        vol = f"volume={bed_db}dB"
    beds, labels, extra_inputs = [], [], []
    fc_parts = []
    ninput = 2 if vid_from else 1     # при --video-from вход 1 занят видео-файлом
    for i, tag in enumerate(tags):
        f = pick_file(tag, os.path.basename(src) + tag)
        if f:
            extra_inputs += ["-stream_loop", "-1", "-i", f]
            fc_parts.append(f"[{ninput}:a]atrim=duration={D:.2f},{vol},"
                            f"afade=t=in:d=0.6,afade=t=out:st={max(0,D-1.5):.2f}:d=1.5[b{i}]")
            ninput += 1
            print(f"  {tag}: {os.path.basename(f)}")
        elif tag in SYNTH:
            fc_parts.append(f"{SYNTH[tag]},atrim=duration={D:.2f},{vol},"
                            f"afade=t=in:d=0.6,afade=t=out:st={max(0,D-1.5):.2f}:d=1.5[b{i}]")
            print(f"  {tag}: SYNTH-заглушка (библиотека пуста)")
        else:
            print(f"  {tag}: нет файлов и нет synth — пропущен"); continue
        labels.append(f"[b{i}]")

    # --accent тег:N — N коротких ВСКРИКОВ (чайки и т.п.) на −6дБ, раскиданы по длине
    # (урок 2026-08-08: чайки непрерывным лупом на тихом уровне не читаются — нужны акценты)
    acc = sys.argv[sys.argv.index("--accent") + 1] if "--accent" in sys.argv else None
    if acc:
        atag, an = acc.split(":")[0], int(acc.split(":")[1])
        af = pick_file(atag, os.path.basename(src) + atag + "acc")
        if af:
            for k in range(an):
                t_at = D * (k + 1) / (an + 1)
                extra_inputs += ["-i", af]
                fc_parts.append(f"[{ninput}:a]atrim=start={5*k}:duration=5,afade=t=in:d=0.8,"
                                f"afade=t=out:st=4:d=1,volume=-6dB,"
                                f"adelay={int(t_at*1000)}|{int(t_at*1000)}[b{len(labels)}]")
                labels.append(f"[b{len(labels)}]"); ninput += 1
            print(f"  акценты {atag}: {an} шт по 5с (-6дБ) из {os.path.basename(af)}")
    if not labels:
        print("нет подложек — выход"); return
    mix_in = "[0:a]" + "".join(labels)
    fc = (";".join(fc_parts) + f";{mix_in}amix=inputs={1+len(labels)}:duration=first:normalize=0,"
          f"alimiter=limit=0.97[aout]")
    if vid_from:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-i", vid_from,
               *extra_inputs, "-filter_complex", fc, "-map", "1:v", "-map", "[aout]",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out]
    else:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src, *extra_inputs,
               "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("ffmpeg ERROR:", (r.stderr or "")[-500:]); return
    print(f"Готово: {os.path.relpath(out, ROOT)}  (подложка {bed_db} дБ, {len(labels)} слоя)")


if __name__ == "__main__":
    main()
