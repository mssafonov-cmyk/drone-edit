# -*- coding: utf-8 -*-
"""Прогон Beat This! на WAV -> work/music_analysis/beats_raw.json ([{time, downbeat}]) + meta.json."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AD = os.environ.get("MUSIC_AD") or os.path.join(ROOT, "work", "music_analysis")
os.makedirs(AD, exist_ok=True)
wav = sys.argv[1] if len(sys.argv) > 1 else os.path.join(AD, "ian_asher.wav")

from beat_this.inference import File2Beats
f2b = File2Beats(device="cpu", dbn=False)
beats, downbeats = f2b(wav)          # массивы времён (сек)
beats = np.asarray(beats, dtype=float); downbeats = np.asarray(downbeats, dtype=float)

def is_db(t):
    return len(downbeats) > 0 and float(np.min(np.abs(downbeats - t))) <= 0.03

raw = [{"time": round(float(t), 4), "downbeat": bool(is_db(t))} for t in beats]
with open(os.path.join(AD, "beats_raw.json"), "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=1)

bpm = 0.0
if len(beats) > 4:
    bpm = round(60.0 / float(np.median(np.diff(beats))), 1)
with open(os.path.join(AD, "meta.json"), "w", encoding="utf-8") as f:
    json.dump({"bpm": bpm, "audio_offset": 0.0, "analyzer": "beat_this-1.1.0"}, f, ensure_ascii=False, indent=1)
print(f"Beat This!: битов {len(beats)}, даунбитов {len(downbeats)}, BPM(медиана) {bpm}")
print("первые биты:", [round(float(t),2) for t in beats[:10]])
print("первые даунбиты:", [round(float(t),2) for t in downbeats[:6]])
