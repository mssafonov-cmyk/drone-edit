# -*- coding: utf-8 -*-
"""Токен-точный пересбор: синтезирует MUSIC_AD/cut_plan.json из архивного shots.json
(dur_frames + start_time), чтобы assemble_from_plan отрендерил ровно те же слоты.
CLI: python gen_cutplan_from_shots.py <shots.json>
"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
AD = os.environ.get("MUSIC_AD")
assert AD, "нужен MUSIC_AD"
sj = json.load(open(sys.argv[1], encoding="utf-8"))
shots = sj["shots"] if isinstance(sj, dict) else sj
st0 = sj.get("start_time", 0.0) if isinstance(sj, dict) else 0.0
fps = 30000 / 1001
plan_shots, t = [], st0
for s in shots:
    dur = s["dur_frames"] / fps
    plan_shots.append({"dur_frames": s["dur_frames"],
                       "start_time": round(t, 3), "end_time": round(t + dur, 3)})
    t += dur
plan = {"start_time": st0, "shots": plan_shots}
out = os.path.join(AD, "cut_plan.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"cut_plan: {len(shots)} слотов, start={plan['start_time']} -> {out}")
