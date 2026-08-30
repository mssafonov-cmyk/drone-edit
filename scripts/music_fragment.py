# -*- coding: utf-8 -*-
"""
Выбор ЛУЧШЕГО ФРАГМЕНТА трека под ролик (доктрина 2026-08-09: не с 00:00, не самый
энергичный участок; хук в первые 1-2с, внутреннее развитие, финал на ослабление фразы).

Скоринг скользящего окна длиной T по фичам (librosa):
  hook      — энергия и онсеты в первые 2с (не пустое интро)
  develop   — рост слоёв внутри окна (вторая половина богаче первой) + точка раскрытия
  not_peak  — штраф за попадание в топ-15% энергии трека (нужен lazy groove, не дроп)
  laidback  — штраф за плотную быструю перкуссию (высокая онсет-плотность)
  vocal     — присутствие вокала (энергия 300–3000 Гц) — мелодичность
  ending    — конец окна на спаде/паузе (ослабление аранжировки) + снэп к фразе
Границы окна снэпятся к даунбитам Beat This! если есть beats_raw.json в MUSIC_AD.

CLI: python music_fragment.py <track.mp3|wav> <target_dur_sec> [--top 3]
Выход: топ-фрагменты с mm:ss и разбором почему.
"""
import os, sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
import librosa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mmss(t):
    return f"{int(t//60):02d}:{int(t%60):02d}"


def main():
    path, T = sys.argv[1], float(sys.argv[2])
    top_n = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 3
    y, sr = librosa.load(path, sr=22050, mono=True)
    dur = len(y) / sr
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    times = librosa.times_like(rms, sr=sr, hop_length=hop)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop, units="time")
    # вокал-полоса 300-3000 Гц
    S = np.abs(librosa.stft(y, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr)
    vb = S[(freqs >= 300) & (freqs <= 3000)].mean(axis=0)
    vb = vb / (vb.max() + 1e-9)
    rms_s = librosa.util.normalize(rms)

    def seg(arr, t0, t1):
        i0, i1 = np.searchsorted(times, [t0, t1])
        return arr[i0:max(i0 + 1, i1)]

    # даунбиты для снэпа (если анализ есть)
    ad = os.environ.get("MUSIC_AD")
    db = None
    if ad and os.path.exists(os.path.join(ad, "beats_raw.json")):
        beats = json.load(open(os.path.join(ad, "beats_raw.json"), encoding="utf-8"))
        db = np.array([b["time"] for b in beats if b.get("downbeat")])

    e_track = rms_s
    hi_thr = np.percentile(e_track, 85)
    cands = []
    starts = db[db < dur - T - 1] if db is not None and len(db) else np.arange(2, dur - T - 1, 1.0)
    for st in starts:
        en = st + T
        w_rms = seg(rms_s, st, en); w_on = [o for o in onsets if st <= o < en]
        if len(w_rms) < 10:
            continue
        head = seg(rms_s, st, st + 2.0)
        hook = float(head.mean() / (np.median(rms_s) + 1e-9))     # >1 = живой вход
        # НЕ РВАТЬ СЛОВО (урок 2026-08-10: фрагмент начался с обрывка вокала): перед
        # стартом вокальная полоса должна быть ТИХОЙ (пауза между фразами), иначе штраф.
        pre_v = seg(vb, max(0, st - 0.5), st)
        in_v = seg(vb, st, st + 1.0)
        word_cut = float(pre_v.mean()) > 0.35 and float(pre_v.mean()) > float(in_v.mean()) * 0.7
        hook_on = 1.0 if any(st <= o <= st + 2.0 for o in onsets) else 0.0
        h1 = w_rms[:len(w_rms)//2].mean(); h2 = w_rms[len(w_rms)//2:].mean()
        develop = float((h2 - h1) / (h1 + 1e-9))                  # рост во 2-й половине
        dsm = np.convolve(w_rms, np.ones(40)/40, "same")
        develop_pt = float(np.max(np.diff(dsm))) * 100            # точка раскрытия
        peak_frac = float((w_rms > hi_thr).mean())                # доля топ-энергии — штраф
        odens = len(w_on) / T                                     # онсетов/с — busy=спешка
        vocal = float(seg(vb, st, en).mean())
        tail = seg(rms_s, en - 1.5, en); after = seg(rms_s, en, min(dur, en + 1.5))
        ending = float((tail.mean() - after.mean()) + (w_rms.mean() - tail.mean()) * 0.5)
        score = (min(hook, 1.4) * 1.2 + hook_on * 0.5
                 + np.clip(develop, -0.5, 0.6) * 1.5 + min(develop_pt, 1.2) * 0.5
                 - peak_frac * 1.8
                 - max(0.0, odens - 3.2) * 0.35
                 + vocal * 0.8
                 - (1.2 if word_cut else 0.0)
                 + np.clip(ending, -0.3, 0.4) * 0.8)
        cands.append((float(st), score, hook, develop, peak_frac, odens, vocal, ending))
    cands.sort(key=lambda x: -x[1])
    # прорежаем близкие старты
    out = []
    for c in cands:
        if all(abs(c[0] - o[0]) > T * 0.5 for o in out):
            out.append(c)
        if len(out) >= top_n:
            break
    print(f"Трек {os.path.basename(path)}  ({mmss(dur)}), цель {T:.0f}с. Топ-{len(out)} фрагментов:")
    for st, sc, hook, dev, pk, od, vo, en_ in out:
        print(f"  {mmss(st)}–{mmss(st+T)}  score {sc:.2f} | вход {hook:.2f} | развитие {dev:+.2f} | "
              f"топ-энергия {pk*100:.0f}% | онсетов/с {od:.1f} | вокал {vo:.2f} | финал-спад {en_:+.2f}")


if __name__ == "__main__":
    main()
