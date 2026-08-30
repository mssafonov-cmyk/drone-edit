# -*- coding: utf-8 -*-
"""
Фильм v5: границы планов ТОЛЬКО по СИЛЬНЫМ долям (энергия top-40%), плотность склеек
следует энергии трека (урок 2026-08-08: пользователь слышит слабые доли как «не в бит»;
«лучше на сильных резать» + «поактивнее на быстрых участках»).

Длина плана от локальной энергии: тихо ~5.5с, средне ~3.6с, драйв ~2.4с.
Выход: cut_plan.json (MUSIC_AD) + печать карты слотов (длина/класс энергии).
CLI: python plan_film_v5.py --start 12 --end 174 --finale-len 8.2
"""
import os, sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AD = os.environ.get("MUSIC_AD") or os.path.join(ROOT, "work", "music_analysis")
FPS = 30000 / 1001


def arg(f, d):
    return float(sys.argv[sys.argv.index(f) + 1]) if f in sys.argv else d


def main():
    sb = json.load(open(os.path.join(AD, "strong_beats.json"), encoding="utf-8"))["beats"]
    strong = [b for b in sb if b["strong"]]
    st_t = np.array([b["time"] for b in strong]); st_e = np.array([b["energy"] for b in strong])
    t0, t1 = arg("--start", 12.0), arg("--end", 174.0)
    fin_len = arg("--finale-len", 8.2)
    # локальная энергия: скользящее среднее энергии сильных долей ±4с
    def local_energy(t):
        m = np.abs(st_t - t) < 4.0
        return float(st_e[m].mean()) if m.any() else float(st_e.mean())
    e_all = np.array([local_energy(t) for t in np.arange(t0, t1, 2.0)])
    q1, q2 = np.percentile(e_all, 40), np.percentile(e_all, 75)

    def shot_len(t):
        e = local_energy(t)
        return 5.5 if e < q1 else (3.6 if e < q2 else 2.4)

    # старт — сильная доля рядом с t0
    i0 = int(np.argmin(np.abs(st_t - t0)))
    bounds = [strong[i0]]
    cls = []
    t = strong[i0]["time"]
    while t < t1 - fin_len - 1.0:
        L = shot_len(t)
        # следующая граница: СИЛЬНАЯ доля, ближайшая к t+L (окно ±0.8с; если пусто — сильнейшая в ±1.4с)
        tgt = t + L
        win = [b for b in strong if abs(b["time"] - tgt) <= 0.8 and b["time"] > t + 1.2]
        if not win:
            win = [b for b in strong if abs(b["time"] - tgt) <= 1.4 and b["time"] > t + 1.2]
        if not win:
            win = [b for b in strong if b["time"] > t + 1.2][:1]
        if not win:
            break
        nb = max(win, key=lambda b: b["energy"])
        bounds.append(nb); cls.append("H" if L < 3 else ("M" if L < 5 else "C"))
        t = nb["time"]
    # финал: фикс. длина (окно idx28), конец НЕ обязан быть на доле (уход в солнце)
    fin_end = t + fin_len
    shots = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i]["time"], bounds[i + 1]["time"]
        shots.append({"shot": i + 1, "start_time": round(a, 4), "end_time": round(b, 4),
                      "start_frame": round(a * FPS), "end_frame": round(b * FPS),
                      "dur_sec": round(b - a, 3),
                      "dur_frames": round(b * FPS) - round(a * FPS), "cls": cls[i]})
    shots.append({"shot": len(shots) + 1, "start_time": round(t, 4), "end_time": round(fin_end, 4),
                  "start_frame": round(t * FPS), "end_frame": round(fin_end * FPS),
                  "dur_sec": round(fin_len, 3),
                  "dur_frames": round(fin_end * FPS) - round(t * FPS), "cls": "FIN"})
    plan = {"fps": 29.97, "start_time": bounds[0]["time"],
            "total_dur": round(fin_end - bounds[0]["time"], 2), "shots": shots}
    json.dump(plan, open(os.path.join(AD, "cut_plan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    n_h = sum(1 for s in shots if s["cls"] == "H"); n_m = sum(1 for s in shots if s["cls"] == "M")
    n_c = sum(1 for s in shots if s["cls"] == "C")
    print(f"планов {len(shots)} (драйв {n_h} / средних {n_m} / спокойных {n_c} / финал 1), "
          f"общая {plan['total_dur']:.1f}с, старт {plan['start_time']:.2f}с")
    print("карта:", "".join(s["cls"][0] for s in shots))


if __name__ == "__main__":
    main()
