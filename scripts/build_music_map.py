# -*- coding: utf-8 -*-
"""
Строит music_map.json из ВНЕШНЕГО бит-анализа (Beat This! / All-In-One), НЕ из оценки LLM.

Вход (work/music_analysis/):
  beats_raw.json  — [{"time": сек, "downbeat": true/false}, ...]  (из Beat This!)
  sections.json   — [{"start": сек, "end": сек, "label": "intro|verse|chorus|bridge|outro"}]  (опц., All-In-One)
  meta.json       — {"bpm": число, "audio_offset": сек}  (опц.)

Выход: work/music_analysis/music_map.json
  {fps, audio_offset, bpm, beats:[{time,frame,beat_in_bar,bar,type}], sections:[], allowed_cut_points:[]}

Уровни точек монтажа (allowed_cut_points):
  A — смена секции (границы sections)
  B — начало 4/8-тактовой фразы (downbeat, кратный phrase_bars от первого downbeat)
  C — downbeat
  D — обычный beat / яркий transient
"""
import os, sys, json, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AD = os.environ.get("MUSIC_AD") or os.path.join(ROOT, "work", "music_analysis")
FPS = 29.97
PHRASE_BARS = 4          # фраза = 4 такта (для уровня B); 8 тоже помечаем


def load(name, default):
    p = os.path.join(AD, name)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def main():
    raw = load("beats_raw.json", None)
    if not raw:
        print("НЕТ work/music_analysis/beats_raw.json — сначала прогнать Beat This!"); return
    sections = load("sections.json", [])
    meta = load("meta.json", {})
    audio_offset = float(meta.get("audio_offset", 0.0))
    bpm = float(meta.get("bpm", 0))

    raw = sorted(raw, key=lambda b: b["time"])
    f2 = lambda t: int(round(t * FPS))

    # такт/доля: downbeat -> beat_in_bar=1, bar++. Доли между даунбитами нумеруем.
    beats = []
    bar = 0; bib = 0
    for b in raw:
        t = float(b["time"]); down = bool(b.get("downbeat", False))
        if down or bar == 0:
            bar += 1; bib = 1
        else:
            bib += 1
        beats.append({"time": round(t, 4), "frame": f2(t), "beat_in_bar": bib,
                      "bar": bar, "type": "downbeat" if (down or (bib == 1)) else "beat"})

    downbeats = [b for b in beats if b["type"] == "downbeat"]
    # BPM из даунбитов/битов, если не дан
    if not bpm and len(beats) > 4:
        import statistics
        d = [beats[i+1]["time"] - beats[i]["time"] for i in range(len(beats)-1)]
        med = statistics.median(d)
        bpm = round(60.0 / med, 1) if med > 0 else 0

    # allowed_cut_points по уровням
    sec_starts = [round(float(s["start"]), 3) for s in sections]
    acp = []
    def add(t, frame, level, mtype, extra=None):
        e = {"time": round(t, 4), "frame": frame, "level": level, "marker_type": mtype}
        if extra: e.update(extra)
        acp.append(e)
    # первый downbeat — якорь для нумерации фраз
    db0 = downbeats[0]["bar"] if downbeats else 1
    for b in beats:
        if b["type"] == "downbeat":
            phrase = (b["bar"] - db0) % PHRASE_BARS == 0
            phrase8 = (b["bar"] - db0) % (PHRASE_BARS * 2) == 0
            is_sec = any(abs(b["time"] - st) <= 0.12 for st in sec_starts)
            if is_sec:
                add(b["time"], b["frame"], "A", "section", {"bar": b["bar"]})
            elif phrase:
                add(b["time"], b["frame"], "B", "phrase", {"bar": b["bar"], "phrase8": phrase8})
            else:
                add(b["time"], b["frame"], "C", "downbeat", {"bar": b["bar"]})
        else:
            add(b["time"], b["frame"], "D", "beat", {"bar": b["bar"], "beat_in_bar": b["beat_in_bar"]})
    acp.sort(key=lambda x: x["time"])

    mmap = {"fps": FPS, "audio_offset": audio_offset, "bpm": bpm,
            "beats": beats, "sections": sections, "allowed_cut_points": acp}
    out = os.path.join(AD, "music_map.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(mmap, f, ensure_ascii=False, indent=1)
    na = sum(1 for x in acp if x["level"] == "A"); nb = sum(1 for x in acp if x["level"] == "B")
    nc = sum(1 for x in acp if x["level"] == "C"); nd = sum(1 for x in acp if x["level"] == "D")
    print(f"music_map.json: битов {len(beats)}, даунбитов {len(downbeats)}, BPM {bpm}, "
          f"секций {len(sections)}")
    print(f"allowed_cut_points: A(секция)={na}, B(фраза)={nb}, C(downbeat)={nc}, D(beat)={nd}")
    print(f"-> {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
