# -*- coding: utf-8 -*-
"""shots.json (сайдкар сборки — ИСТИНА о рендере) -> cut_plan-формат для грейда.
Причина: cut_plan.json в папках музыки перезаписывается следующими сборками (Гонконг
затёр odesza-план вьетнамского фильма) — грейд по нему даёт кадры чужого плана (вспышки).
CLI: python shots_to_cutplan.py <in.shots.json> <out.json>"""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sj = json.load(open(sys.argv[1], encoding="utf-8"))
shots = sj["shots"]; a0 = float(sj.get("start_time", 0.0))
fps = 30000 / 1001
out, acc_t, acc_f = [], a0, round(a0 * fps)
for s in shots:
    st_t, st_f = acc_t, acc_f
    acc_f = st_f + s["dur_frames"]
    acc_t = st_t + s["dur_sec"]
    out.append({"shot": s["slot"], "start_time": round(st_t, 4), "end_time": round(acc_t, 4),
                "start_frame": st_f, "end_frame": acc_f,
                "dur_sec": s["dur_sec"], "dur_frames": s["dur_frames"]})
json.dump({"fps": 29.97, "start_time": a0, "total_dur": round(acc_t - a0, 2), "shots": out},
          open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(out)} планов -> {sys.argv[2]}")
