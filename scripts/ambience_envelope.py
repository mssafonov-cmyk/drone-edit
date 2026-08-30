# -*- coding: utf-8 -*-
"""
Огибающая громкости ambient по ДАЛЬНОСТИ объекта (просьба 2026-08-08: «рядом с
водопадом шумит, издалека — тише»). По каждому плану меряем «величину» источника
звука в кадре -> громкость подложки на этом плане, плавные переходы 0.8с.

Детекторы prominence (0..1): waterfall/surf/ocean — доля БЕЛОЙ ПЕНЫ (яркие
малонасыщенные пиксели); river — то же; прочие теги — константа (нет объекта).

CLI: python ambience_envelope.py <shots.json> <render.mp4> <тег> <out_env.json>
Выход: {"points":[{"t":сек,"db":дБ},...]} — сетка для volume-выражения.
"""
import os, sys, json, subprocess
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import yaml, cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOAMY = ("waterfall", "surf", "river")
DB_NEAR, DB_FAR = -5.0, -13.0     # близко/далеко (2026-08-08: шире и громче — телефон глушит)


def prominence(img, tag):
    """0..1 «величина источника звука в кадре» по тегу (урок: пена не работает для
    спокойной воды — Халонг весь ушёл в 'далеко')."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    if tag in FOAMY:                      # пена: яркое малонасыщенное
        return float(((v > 185) & (s < 70)).mean())
    if tag == "ocean":                    # ВОДА: сине-циановые + пена
        water = ((h > 80) & (h < 135) & (s > 40) & (v > 40)).mean()
        foam = ((v > 185) & (s < 70)).mean()
        return float(water + foam)
    if tag in ("jungle", "birds"):        # зелень
        return float(((h > 35) & (h < 85) & (s > 60) & (v > 40)).mean())
    return 0.5                            # wind/city: константа


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    ff = cfg["tools"]["ffmpeg"]
    sj = json.load(open(sys.argv[1], encoding="utf-8"))
    render, tag, out_p = sys.argv[2], sys.argv[3], sys.argv[4]
    shots = sj["shots"]
    tmp = os.path.join(ROOT, "work", "_envchk"); os.makedirs(tmp, exist_ok=True)
    mids, acc = [], 0.0
    for s in shots:
        mids.append(acc + s["dur_sec"] / 2); acc += s["dur_sec"]
    proms = []
    for t in mids:
        p = os.path.join(tmp, "f.png")
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", render, "-frames:v", "1", "-vf", "scale=240:-2", p], capture_output=True)
        im = cv2.imread(p)
        proms.append(prominence(im, tag.split(",")[0]) if im is not None else 0.5)
    pr = np.array(proms)
    if pr.max() - pr.min() < 1e-4:
        norm = np.full_like(pr, 0.5)
    else:
        norm = (pr - pr.min()) / (pr.max() - pr.min())
    dbs = DB_FAR + (DB_NEAR - DB_FAR) * norm
    # точки: центры планов; между ними volume-выражение интерполирует
    points = [{"t": round(t, 2), "db": round(float(d), 1)} for t, d in zip(mids, dbs)]
    json.dump({"points": points, "tag": tag}, open(out_p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("огибающая:", " ".join(f"{p['t']:.0f}с:{p['db']:.0f}дБ" for p in points))


if __name__ == "__main__":
    main()
