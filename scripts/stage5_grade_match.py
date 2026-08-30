# -*- coding: utf-8 -*-
"""
Этап 5 v2 — Цветокор по двухэтапной схеме (shot matching + единый лук).

ПОЧЕМУ ТАК. Один глобальный грейд на весь ролик не работает: планы сняты в
разных условиях, и лук складывается с тем, что уже есть в кадре. Тёплый
рассветный план + тёплый лук = ядовитая желтизна; блёклый план тот же лук почти
не замечает. Вывод из разбора с пользователем (2026-07-20).

РЕШЕНИЕ (как делают колористы):
  Этап 1 — ПЕРВИЧНАЯ КОРРЕКЦИЯ, СВОЯ ДЛЯ КАЖДОГО ПЛАНА. Каждый план приводится
    к общей базе: баланс белого, яркость, насыщенность. Эталон — МЕДИАНА по
    самому ролику (ролик приводится к самому себе, выбросы подтягиваются).
    Тёплый план тянется к нейтрали, блёклый — вверх. Сила коррекции `strength`
    (0 = не трогать, 1 = полностью выровнять; 0.7 по умолчанию — убирает
    выбросы, но оставляет естественную разницу золотого часа и полудня).
  Этап 2 — ТВОРЧЕСКИЙ ЛУК, ОДИНАКОВЫЙ ДЛЯ ВСЕХ. Одна подпись поверх уже
    выровненного материала -> ложится ровно везде, ролик = одна плёнка.

Границы планов берутся из .otio, собранного этапом 4 — то есть коррекция знает
реальную нарезку и не мажет через склейку.

Использование:
  python stage5_grade_match.py <preview.mp4> <timeline.otio> <out.mp4> [look] [strength]
    look: signature (по умолч.) | warm | cool | neutral
"""
import os, sys, json, subprocess
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- творческие луки (этап 2). Применяются к УЖЕ выровненному материалу,
# поэтому мягче, чем в первой версии — иначе складывается и «ядовитит».
def build_look(name, warm_scale=1.0):
    """Творческий лук. Холодная часть (тил в тенях) — константа, она безопасна.
    ТЁПЛАЯ часть в светах масштабируется `warm_scale`: чем теплее план уже сам
    по себе, тем меньше тепла добавляем. Это лечит «ядовитую желтизну» на
    рассветных кадрах, не отнимая у них золото (см. correction_filter)."""
    ws = max(0.0, min(1.0, warm_scale))
    if name == "warm":
        return (f"colorbalance=rs=0.02:bs=-0.02:rh={0.05*ws:.3f}:gh={0.02*ws:.3f}:bh={-0.06*ws:.3f},"
                "curves=all='0/0.02 0.5/0.52 1/0.99',eq=contrast=1.04:saturation=1.0")
    if name == "cool":
        return ("colorbalance=rs=-0.05:gs=0.03:bs=0.09:"
                f"rh={0.02*ws:.3f}:bh={-0.02*ws:.3f},"
                "curves=r='0/0.02 0.25/0.23 1/0.96':b='0/0.04 0.5/0.51 1/0.98',"
                "eq=contrast=1.08:saturation=0.82")
    if name == "neutral":
        return "eq=contrast=1.02:saturation=0.95"
    # signature — целевой дрон-лук (work/color_target_spec.md), пользователь
    # выбрал вариант A как базу (2026-07-20)
    return (f"colorbalance=rs=-0.03:gs=0.02:bs=0.06:"
            f"rh={0.05*ws:.3f}:gh={0.015*ws:.3f}:bh={-0.05*ws:.3f},"
            "curves=r='0/0.015 0.25/0.245 0.75/0.775 1/0.975':b='0/0.03 0.5/0.49 1/0.96',"
            "eq=contrast=1.05:saturation=0.9")


def load_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def refine_bounds(cfg, video, otio_bounds, fps=29.97, search_sec=0.7):
    """Границы планов = ориентир из .otio, ПОДТЯНУТЫЙ к реальному скачку в видео.

    Чистая автодетекция ненадёжна (соседние похожие планы — напр. зелёный карст
    в рилсе 1 — не дают заметного скачка и склейка теряется). Чистый .otio тоже
    врёт: реальный рендер уходит от таймлайна (ретайм, fps-ресемплинг) до ~0.3с,
    и коррекция заезжает на соседний план -> цвет «моргает» ВНУТРИ плана
    (найдено на 53-54с рилса 2). Комбинация решает обе беды: знаем, ГДЕ примерно
    склейка, и находим её ТОЧНЫЙ кадр рядом.
    """
    ffmpeg = cfg["tools"]["ffmpeg"]
    w, h = 96, 170
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", video,
           "-vf", f"scale={w}:{h},fps={fps}", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True).stdout
    n = len(out) // (w * h)
    if n < 10:
        return otio_bounds, 0
    fr = np.frombuffer(out[:n * w * h], np.uint8).reshape(n, h, w).astype(np.int16)
    diff = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))   # diff[i] = между кадром i и i+1

    total_video = n / fps
    total_otio = otio_bounds[-1][1]
    scale = total_video / total_otio if total_otio > 0 else 1.0
    rad = int(search_sec * fps)

    snapped, moved = [0.0], 0
    for (t0, t1) in otio_bounds[:-1]:
        guess = int(round(t1 * scale * fps))
        lo, hi = max(1, guess - rad), min(len(diff) - 1, guess + rad)
        if hi <= lo:
            snapped.append(t1 * scale)
            continue
        k = int(np.argmax(diff[lo:hi])) + lo
        # доверяем только выраженному скачку, иначе оставляем масштабированный ориентир
        local_med = float(np.median(diff[lo:hi]))
        if diff[k] > max(3.0, local_med * 2.5):
            snapped.append((k + 1) / fps)
            if abs((k + 1) / fps - t1 * scale) > 1.5 / fps:
                moved += 1
        else:
            snapped.append(t1 * scale)
    snapped.append(total_video)
    bounds = [(snapped[i], snapped[i + 1]) for i in range(len(snapped) - 1)]
    return bounds, moved


def detect_cuts(cfg, video, expected=None, fps=29.97):
    """Границы планов ПО САМОМУ ВИДЕО (пики покадровой разницы).

    Брать их из .otio НЕЛЬЗЯ: реальный рендер расходится с таймлайном (ретайм,
    fps-ресемплинг), и на длинном ролике расхождение доходило до ~0.3с = 8
    кадров. Тогда коррекция следующего плана заезжает на хвост предыдущего и
    цвет «моргает» ВНУТРИ плана — пользователь это увидел на 53-54с рилса 2.
    """
    ffmpeg = cfg["tools"]["ffmpeg"]
    w, h = 96, 170
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", video,
           "-vf", f"scale={w}:{h},fps={fps}", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True).stdout
    n = len(out) // (w * h)
    if n < 10:
        return None
    fr = np.frombuffer(out[:n * w * h], np.uint8).reshape(n, h, w).astype(np.int16)
    diff = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    med = float(np.median(diff))
    thr = max(6.0, med * 4.0)
    cuts = [i + 1 for i, d in enumerate(diff) if d > thr]
    # склеиваем соседние срабатывания
    merged = []
    for c in cuts:
        if merged and c - merged[-1] <= 2:
            continue
        merged.append(c)
    bounds, prev = [], 0
    for c in merged:
        bounds.append((prev / fps, c / fps))
        prev = c
    bounds.append((prev / fps, n / fps))
    if expected and abs(len(bounds) - expected) > max(1, expected * 0.2):
        return None          # детекция явно промахнулась — пусть решает вызывающий
    return bounds


def shot_bounds_from_otio(otio_path):
    """Границы планов на таймлайне (сек) из .otio этапа 4."""
    with open(otio_path, "r", encoding="utf-8") as f:
        tl = json.load(f)
    track = next(t for t in tl["tracks"]["children"] if t.get("kind") == "Video")
    bounds, t = [], 0.0
    for clip in track["children"]:
        d = clip["source_range"]["duration"]
        dur = float(d["value"]) / float(d["rate"])
        bounds.append((t, t + dur))
        t += dur
    return bounds


def measure(cfg, video, t0, t1, n=3):
    """Средние R,G,B, яркость и насыщенность плана (несколько кадров)."""
    ffmpeg = cfg["tools"]["ffmpeg"]
    w, h = 160, 284
    dur = max(0.2, t1 - t0)
    step = dur / (n + 1)
    acc = []
    for k in range(1, n + 1):
        t = t0 + step * k
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
               "-i", video, "-frames:v", "1", "-vf", f"scale={w}:{h}",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        out = subprocess.run(cmd, capture_output=True).stdout
        if len(out) < w * h * 3:
            continue
        img = np.frombuffer(out[:w * h * 3], np.uint8).reshape(h, w, 3).astype(np.float32)
        acc.append(img)
    if not acc:
        return None
    img = np.mean(acc, axis=0)
    r, g, b = img[..., 0].mean(), img[..., 1].mean(), img[..., 2].mean()
    luma = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    mx = img.max(axis=2); mn = img.min(axis=2)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0).mean() * 255
    return {"r": float(r), "g": float(g), "b": float(b),
            "luma": float(luma.mean()), "sat": float(sat)}


def correction_filter(st, ref, strength=0.7):
    """Первичная коррекция плана к эталону.

    ВАЖНО (вывод из разбора 2026-07-20): выравнивать можно ЭКСПОЗИЦИЮ и
    НАСЫЩЕННОСТЬ — это техническая консистентность. А баланс белого выравнивать
    «в среднее по ролику» НЕЛЬЗЯ: медиана travel-ролика обычно зелёная (дневной
    пейзаж), и закат/золотой час от такого выравнивания ЗЕЛЕНЕЕТ — золото уходит.
    Тёплый закат тёплый не по ошибке, а по сути. Поэтому WB правим лишь слегка
    (гасим грубые касты), а перегрев лечится на этапе лука — тёплая составляющая
    лука масштабируется обратно пропорционально уже имеющемуся теплу плана."""
    def gain(cur, target, s, lo, hi):
        if cur <= 1e-6:
            return 1.0
        return float(min(hi, max(lo, (target / cur) ** s)))

    # 1) экспозиция — общий множитель на все каналы. Единственное, что можно
    #    выравнивать смело: яркость плана — это техника, а не замысел.
    g_luma = gain(st["luma"], ref["luma"], strength, 0.85, 1.18)

    # 2) баланс белого — ОЧЕНЬ мягко (0.15) и в узком коридоре: гасим только
    #    грубый каст. Сильнее нельзя — золотой час позеленеет (проверено).
    m_shot = max(1e-6, (st["r"] + st["g"] + st["b"]) / 3.0)
    m_ref = max(1e-6, (ref["r"] + ref["g"] + ref["b"]) / 3.0)
    wb = {}
    for ch in ("r", "g", "b"):
        wb[ch] = gain(st[ch] / m_shot, ref[ch] / m_ref, 0.15, 0.96, 1.04)

    gr = g_luma * wb["r"]
    gg = g_luma * wb["g"]
    gb = g_luma * wb["b"]
    # 3) насыщенность — мягко (0.3). У заката она высокая ЗАКОННО (это золото,
    #    а не пересыщение); душить её до медианы = убить кадр.
    gs = gain(st["sat"], ref["sat"], 0.3, 0.86, 1.2)
    # colorlevels принимает только 0..1. Осветление делаем через вход
    # (imax = 1/gain < 1), затемнение — через выход (omax = gain < 1).
    parts = []
    for ch, g in (("r", gr), ("g", gg), ("b", gb)):
        if g >= 1.0:
            parts.append(f"{ch}imax={min(1.0, 1.0/g):.4f}")
        else:
            parts.append(f"{ch}omax={g:.4f}")
    f = "colorlevels=" + ":".join(parts)
    if abs(gs - 1.0) > 0.01:
        f += f",eq=saturation={gs:.3f}"
    return f, {"gr": gr, "gg": gg, "gb": gb, "gs": gs}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 4:
        print(__doc__); return
    preview, otio_path, out_mp4 = sys.argv[1], sys.argv[2], sys.argv[3]
    look_name = sys.argv[4] if len(sys.argv) > 4 else "signature"
    strength = float(sys.argv[5]) if len(sys.argv) > 5 else 0.7
    # ПО-ПЛАНОВЫЙ лук: если в аргументе список через запятую (длиной = число планов),
    # каждый план красится своим луком (пользователь: гора/рассвет -> оранж,
    # зелень -> cool). Иначе один лук на весь ролик.
    looks_list = [x.strip() for x in look_name.split(",")] if "," in look_name else None
    cfg = load_cfg()
    ffmpeg = cfg["tools"]["ffmpeg"]

    otio_bounds = shot_bounds_from_otio(otio_path)
    bounds, moved = refine_bounds(cfg, preview, otio_bounds)
    print(f"Планов: {len(bounds)} | границы подтянуты к реальным склейкам "
          f"(сдвинуто {moved} из {len(bounds)-1})")

    stats = []
    for (t0, t1) in bounds:
        st = measure(cfg, preview, t0, t1)
        stats.append(st)
    valid = [s for s in stats if s]
    if not valid:
        print("Не удалось измерить планы"); return
    ref = {k: float(np.median([s[k] for s in valid])) for k in ("r", "g", "b", "luma", "sat")}
    print(f"Эталон (медиана ролика): R{ref['r']:.0f} G{ref['g']:.0f} B{ref['b']:.0f} "
          f"| яркость {ref['luma']:.0f} | насыщ {ref['sat']:.0f}")

    ref_warm = ref["r"] - ref["b"]
    parts, labels = [], []
    for i, ((t0, t1), st) in enumerate(zip(bounds, stats)):
        this_look = looks_list[min(i, len(looks_list) - 1)] if looks_list else look_name
        chain = f"[0:v]trim=start={t0:.3f}:end={t1:.3f},setpts=PTS-STARTPTS"
        if st is None:
            look = build_look(this_look, 1.0)
        else:
            corr, g = correction_filter(st, ref, strength)
            warm_after = st["r"] * g["gr"] - st["b"] * g["gb"]
            # чем теплее план после коррекции, тем слабее тёплая часть лука
            excess = max(0.0, warm_after - ref_warm)
            ws = max(0.15, 1.0 - excess / 35.0)
            look = build_look(this_look, ws)
            chain += "," + corr
            print(f"  план {i+1:2d}: {this_look:9s} тепло {st['r']-st['b']:+5.0f} -> {warm_after:+5.0f} "
                  f"| насыщ {st['sat']:3.0f} x{g['gs']:.2f} | тепло лука x{ws:.2f}")
        chain += "," + look + f"[s{i}]"
        parts.append(chain)
        labels.append(f"[s{i}]")

    fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(bounds)}:v=1:a=0[outv]"

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", preview,
           "-filter_complex", fc, "-map", "[outv]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "fast", "-crf", "18",
           "-pix_fmt", "yuv420p",  # обяз.: иначе x264 выдаёт yuv444p — телефоны не декодируют (чёрный экран)
           "-c:a", "copy", out_mp4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("ffmpeg ERROR:", (r.stderr or "")[-1500:]); return
    print(f"Готово ({look_name}, сила выравнивания {strength}): "
          f"{os.path.relpath(out_mp4, ROOT)}")


if __name__ == "__main__":
    main()
