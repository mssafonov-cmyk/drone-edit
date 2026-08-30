# -*- coding: utf-8 -*-
"""
Планировщик точек реза по СХЕМЕ ТАКТОВ поверх music_map.json (внешний бит-анализ).
Каждая склейка = downbeat на кумулятивном такте от стартового downbeat. ВРЕМЯ абсолютное
(из music_map), КАДР считается независимо: frame = round(time * fps). НЕ складываем
округлённые длительности клипов (правило 5).

Использование:
  python plan_reel_cuts.py --scheme 4,2,2,4,2,2,4,4 [--start-bar N] [--start-time SEC]
Выход: work/music_analysis/cut_plan.json — [{shot, start_time, end_time, start_frame,
        end_frame, start_bar, bars, marker_type}]
"""
import os, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AD = os.environ.get("MUSIC_AD") or os.path.join(ROOT, "work", "music_analysis")


def arg(flag, default=None):
    a = sys.argv
    return a[a.index(flag) + 1] if flag in a else default


def main():
    with open(os.path.join(AD, "music_map.json"), "r", encoding="utf-8") as f:
        mm = json.load(f)
    fps = mm["fps"]
    downbeats = [b for b in mm["beats"] if b["type"] == "downbeat"]
    if len(downbeats) < 4:
        print("мало downbeat-ов в music_map"); return
    scheme = [int(x) for x in (arg("--scheme", "4,2,2,4,2,2,4,4")).split(",")]

    # стартовый downbeat: по --start-bar, или по --start-time (ближайший downbeat), или первый
    start_time = arg("--start-time"); start_bar = arg("--start-bar")
    if start_bar is not None:
        start = next((d for d in downbeats if d["bar"] == int(start_bar)), downbeats[0])
    elif start_time is not None:
        st = float(start_time)
        start = min(downbeats, key=lambda d: abs(d["time"] - st))
    else:
        start = downbeats[0]
    b0 = start["bar"]

    # кумулятивные границы в ЮНИТАХ: [0, 4, 6, 8, 12, 14, 16, 20, 24]
    bounds_u, acc = [0], 0
    for s in scheme:
        acc += s; bounds_u.append(acc)

    target_dur = arg("--target-dur")
    energy_mode = "--energy" in sys.argv
    if target_dur is not None and arg("--beat-pattern") is None:
        td = float(target_dur); usec = td / sum(scheme)
        boundary_times = []; prev_t = -9
        if energy_mode:
            # СНЭП к ЭНЕРГЕТИЧЕСКОМУ ПИКУ: Beat This! даёт сетку, но не силу; даунбит != акцент
            # (проверено). Берём сильнейший по энергии онсетов бит в окне ±0.5с от цели.
            import json as _j
            sb = _j.load(open(os.path.join(AD, "strong_beats.json"), encoding="utf-8"))["beats"]
            for b in sb:
                b["frame"] = int(round(b["time"] * fps))
            for cu in bounds_u:
                tgt = start["time"] + cu * usec
                win = [b for b in sb if abs(b["time"] - tgt) <= 0.5 and b["time"] > prev_t + 0.6]
                if not win:
                    win = [b for b in sb if b["time"] > prev_t + 0.6] or sb
                    b = min(win, key=lambda x: abs(x["time"] - tgt))
                else:
                    b = max(win, key=lambda x: x["energy"])   # сильнейший бит в окне
                prev_t = b["time"]
                boundary_times.append((0, b["time"], b["frame"], "energy"))
        else:
            for cu in bounds_u:
                tgt = start["time"] + cu * usec
                d = min(downbeats, key=lambda x: abs(x["time"] - tgt))
                if d["time"] <= prev_t + 0.05:
                    later = [x for x in downbeats if x["time"] > prev_t + 1.0]
                    d = min(later, key=lambda x: abs(x["time"] - tgt)) if later else d
                prev_t = d["time"]
                boundary_times.append((d["bar"], d["time"], d["frame"], "downbeat"))
    elif (bpat := arg("--beat-pattern")) is not None:
        # ПЕРЕМЕННЫЙ РИТМ: список долей на каждый план ("8,8,4,4,..."). Кумулятивный
        # индекс доли от стартовой доли; граница = РЕАЛЬНАЯ доля в сетке, кадр round(t*fps)
        # независимо. Кратные 2 -> сильная доля/бэкбит; кратные 4 -> downbeat. Даёт «почаще
        # + местами длиннее» без ухода с сетки (правила 5/6, осознанная частая нарезка).
        pat = [int(x) for x in bpat.split(",")]
        allb = mm["beats"]
        cum = [0]
        for p in pat:
            cum.append(cum[-1] + p)
        td = arg("--target-dur")
        boundary_times = []
        if td is not None:
            # веса pat -> доля от target-dur; каждая граница -> БЛИЖАЙШАЯ реальная доля.
            # Ровный тайминг при неровной детекции долей во вступлении (half-time).
            td = float(td); per = td / cum[-1]; prev_t = -9
            for cu in cum:
                tgt = start["time"] + cu * per
                b = min(allb, key=lambda x: abs(x["time"] - tgt))
                if b["time"] <= prev_t + 0.15:              # монотонность
                    later = [x for x in allb if x["time"] > prev_t + 0.30]
                    b = min(later, key=lambda x: abs(x["time"] - tgt)) if later else b
                prev_t = b["time"]
                boundary_times.append((b["bar"], b["time"], b["frame"], b["type"]))
        else:
            si = min(range(len(allb)), key=lambda i: abs(allb[i]["time"] - start["time"]))
            for cu in cum:
                j = si + cu
                if j >= len(allb):
                    print(f"  не хватает долей: нужна доля #{j}, а всего {len(allb)}"); return
                bb = allb[j]
                boundary_times.append((bb["bar"], bb["time"], bb["frame"], bb["type"]))
        scheme = pat  # чтобы 'bars' в плане отражал длину плана в долях
    elif (bpu := arg("--beats-per-unit")) is not None:
        # ЮНИТ = N долей: шаг по всему бит-массиву (downbeat-ы 2.72с слишком крупные под 33с).
        bpu = int(bpu)
        allb = mm["beats"]
        # индекс стартовой доли = ближайшая доля к стартовому downbeat
        si = min(range(len(allb)), key=lambda i: abs(allb[i]["time"] - start["time"]))
        boundary_times = []
        for cu in bounds_u:
            j = si + cu * bpu
            if j >= len(allb):
                print(f"  не хватает долей под юнит {cu} (нужна доля #{j})"); return
            bb = allb[j]
            boundary_times.append((bb["bar"], bb["time"], bb["frame"], bb["type"]))
    else:
        db_by_bar = {d["bar"]: d for d in downbeats}
        boundary_times = []
        for cb in bounds_u:
            d = db_by_bar.get(b0 + cb)
            if d is None:
                print(f"  нет downbeat на такте {b0+cb} — не хватает трека под схему"); return
            boundary_times.append((b0 + cb, d["time"], d["frame"], d.get("marker_type", "downbeat")))

    plan = []
    for i in range(len(boundary_times) - 1):
        bar_i, t0, fr0, _ = boundary_times[i]
        bar_j, t1, fr1, _ = boundary_times[i + 1]
        plan.append({"shot": i + 1, "start_bar": bar_i, "bars": scheme[i],
                     "start_time": round(t0, 4), "end_time": round(t1, 4),
                     "start_frame": fr0, "end_frame": fr1,
                     "dur_sec": round(t1 - t0, 3), "dur_frames": fr1 - fr0,
                     "marker_type": "downbeat"})
    out = os.path.join(AD, "cut_plan.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"fps": fps, "start_bar": b0, "start_time": boundary_times[0][1],
                   "scheme": scheme, "total_dur": round(boundary_times[-1][1] - boundary_times[0][1], 2),
                   "shots": plan}, f, ensure_ascii=False, indent=1)
    print(f"схема {scheme} от такта {b0} (t={boundary_times[0][1]:.2f}с): "
          f"{len(plan)} планов, общая {boundary_times[-1][1]-boundary_times[0][1]:.2f}с")
    for s in plan:
        print(f"  план {s['shot']}: t {s['start_time']:.2f}-{s['end_time']:.2f}  "
              f"кадр {s['start_frame']}-{s['end_frame']}  ({s['bars']} такта, {s['dur_sec']}с)")
    print(f"-> {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
