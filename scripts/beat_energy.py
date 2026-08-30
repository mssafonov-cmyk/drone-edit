# -*- coding: utf-8 -*-
"""
Энергия онсетов на каждом бите Beat This!. Beat This! даёт СЕТКУ (биты/даунбиты),
но НЕ силу. «Сильная доля» = где реально бьёт энергия (кик/спектр.флюкс), а не
метрический даунбит (проверено: даунбиты Ian Asher даже слабее обычных битов).
-> work/music_analysis/<трек>/strong_beats.json: [{time, energy, downbeat}] + порог strong.
Режем по ЭНЕРГЕТИЧЕСКИМ пикам (plan_reel_cuts снэпит к сильнейшему биту в окне).
"""
import os, sys, json
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import librosa
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AD = os.environ.get("MUSIC_AD") or os.path.join(ROOT, "work", "music_analysis")

wav = sys.argv[1] if len(sys.argv) > 1 else None
if not wav:
    for f in os.listdir(AD):
        if f.endswith(".wav"): wav = os.path.join(AD, f); break

y, sr = librosa.load(wav, sr=22050, mono=True)
onset_env = librosa.onset.onset_strength(y=y, sr=sr)
times = librosa.times_like(onset_env, sr=sr)
beats = json.load(open(os.path.join(AD, "beats_raw.json"), encoding="utf-8"))


def energy_at(t):
    i = int(np.argmin(np.abs(times - t)))
    return float(np.max(onset_env[max(0, i - 2):i + 3]))   # локальный пик ±2 фрейма


rows = [{"time": b["time"], "downbeat": bool(b.get("downbeat")), "energy": round(energy_at(b["time"]), 3)}
        for b in beats]
E = np.array([r["energy"] for r in rows])
thr = float(np.percentile(E, 60))     # сильные = верхние 40% по энергии
for r in rows:
    r["strong"] = r["energy"] >= thr
out = os.path.join(AD, "strong_beats.json")
json.dump({"strong_threshold": round(thr, 3), "beats": rows}, open(out, "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
ns = sum(r["strong"] for r in rows)
print(f"strong_beats.json: {len(rows)} битов, {ns} сильных (энергия>={thr:.2f}), "
      f"даунбитов среди сильных {sum(r['strong'] and r['downbeat'] for r in rows)}/{ns}")
print(f"-> {os.path.relpath(out, ROOT)}")
