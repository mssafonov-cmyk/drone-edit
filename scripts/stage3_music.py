# -*- coding: utf-8 -*-
"""
Этап 3 — анализ музыки: темп (BPM), битовая сетка, музыкальные фразы,
энергетические пики. Источник — все треки в music/ (только чтение).

Выход: work/music_grid.json — по каждому треку: tempo_bpm, beats (сек),
phrases (границы фраз по phrase_bars тактов, в сек), energy_peaks (сек).
Склейки этапа 4 должны ставиться на границы фраз, не на каждый бит (правило 5).
"""
import os, sys, json
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import yaml
import librosa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_EXT = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_energy_peaks(y, sr, min_gap_sec=1.2):
    """Локальные пики RMS-энергии (акценты/дропы) не чаще min_gap_sec."""
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    if len(rms) < 5:
        return []
    k = 5
    rms_s = np.convolve(rms, np.ones(k) / k, mode="same")
    times = librosa.frames_to_time(np.arange(len(rms_s)), sr=sr, hop_length=hop)
    thresh = float(np.mean(rms_s) + 0.5 * np.std(rms_s))

    peaks = []
    for i in range(1, len(rms_s) - 1):
        if rms_s[i] > rms_s[i - 1] and rms_s[i] >= rms_s[i + 1] and rms_s[i] > thresh:
            peaks.append((float(times[i]), float(rms_s[i])))

    peaks.sort(key=lambda p: -p[1])
    kept = []
    for t, v in peaks:
        if all(abs(t - kt) >= min_gap_sec for kt in kept):
            kept.append(t)
    return sorted(round(t, 2) for t in kept)


def analyze_track(path, phrase_bars, beats_per_bar):
    y, sr = librosa.load(path, sr=22050, mono=True)
    duration = len(y) / sr

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    # ТЕМП/БИТЫ — через y=y (не onset_envelope): onset_env-путь занижал темп в ~1.5x
    # (Ian Asher: давал 117.5 вместо реальных 172 -> вся сетка мимо пульса, рилс «не в бит»).
    # Проверено: beat_track(y=y) даёт верные 172; Woodkid остаётся 123.
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    if not beat_times or beat_times[0] > 0.05:
        beat_times = [0.0] + beat_times  # первая фраза начинается с начала трека

    # РЕЗАТЬ НАДО НА ОНСЕТАХ (спектральных ударах), а не на бит-сетке. Бит-трекинг
    # даёт позиции, смещённые на 30-60мс от реального транзиента -> склейка
    # ощущается «не успел»/мимо (доказано измерением видео-склеек vs онсетов в
    # рендере). Онсет = реальный слышимый удар, склейка на нём = «в попад».
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr,
                                               backtrack=False)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    onset_strength = onset_env[np.clip(onset_frames, 0, len(onset_env) - 1)]
    # каждый онсет с его силой (для выбора СИЛЬНЫХ ударов при монтаже)
    onsets = [[round(float(t), 3), round(float(s), 3)]
              for t, s in zip(onset_times, onset_strength)]
    # сильные онсеты — сила выше медианы (акценты/downbeat-и); на них целимся
    thr = float(np.median(onset_strength)) if len(onset_strength) else 0.0
    strong_beats = [round(float(t), 3) for t, s in zip(onset_times, onset_strength)
                    if s >= thr]

    # ПЕРВЫЙ СЛЫШИМЫЙ ЗВУК трека (после тихого интро/фейд-ина) — чтобы монтаж
    # начинался со звука, а не с тишины (пользователь: «музыка с 5 секунды»)
    env_t = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr)
    rms = librosa.feature.rms(y=y)[0]
    rms_t = librosa.frames_to_time(np.arange(len(rms)), sr=sr)
    loud = float(np.percentile(rms, 90))
    first_sound = 0.0
    for tt, r in zip(rms_t, rms):
        if r > loud * 0.15:
            first_sound = float(tt); break

    beats_per_phrase = max(1, phrase_bars * beats_per_bar)
    phrase_starts = beat_times[0::beats_per_phrase]
    if not phrase_starts or phrase_starts[0] > 0.001:
        phrase_starts = [0.0] + phrase_starts

    phrases = []
    for i, st in enumerate(phrase_starts):
        en = phrase_starts[i + 1] if i + 1 < len(phrase_starts) else duration
        if en - st < 0.05:
            continue
        phrases.append({"start": round(st, 3), "end": round(en, 3),
                         "bars": phrase_bars, "dur": round(en - st, 3)})

    return {
        "file": os.path.basename(path),
        "duration_sec": round(duration, 2),
        "tempo_bpm": round(tempo, 1),
        "beats_per_bar": beats_per_bar,
        "beats": [round(b, 3) for b in beat_times],
        "onsets": onsets,                   # [t, сила] всех спектральных ударов
        "strong_beats": strong_beats,       # сильные ОНСЕТЫ — на них резать
        "first_sound": round(first_sound, 3),  # первый слышимый звук трека
        "phrase_bars": phrase_bars,
        "phrases": phrases,
        "energy_peaks": find_energy_peaks(y, sr),
    }


def main():
    cfg = load_cfg()
    music_dir = os.path.join(ROOT, cfg["paths"]["music"])
    phrase_bars = cfg["music"]["phrase_bars"]
    beats_per_bar = cfg["music"]["beats_per_bar"]

    only = set(sys.argv[1:])  # опционально: имена файлов для точечного анализа
    tracks = sorted(f for f in os.listdir(music_dir) if f.lower().endswith(AUDIO_EXT))
    if only:
        tracks = [f for f in tracks if f in only]

    out_path = os.path.join(ROOT, "work", "music_grid.json")
    result = {"phrase_bars": phrase_bars, "beats_per_bar": beats_per_bar, "tracks": {}}
    if only and os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.load(f)

    for f in tracks:
        path = os.path.join(music_dir, f)
        print(f"Анализ: {f}")
        try:
            info = analyze_track(path, phrase_bars, beats_per_bar)
            result["tracks"][f] = info
            n_ph = len(info["phrases"])
            print(f"  BPM {info['tempo_bpm']}, длительность {info['duration_sec']}с, "
                  f"фраз ({phrase_bars} такт.) {n_ph}, пиков энергии {len(info['energy_peaks'])}")
        except Exception as e:
            print(f"  ОШИБКА: {e}")

    with open(out_path, "w", encoding="utf-8") as fo:
        json.dump(result, fo, ensure_ascii=False, indent=2)
    print(f"Готово -> {os.path.relpath(out_path, ROOT)}")


if __name__ == "__main__":
    main()
