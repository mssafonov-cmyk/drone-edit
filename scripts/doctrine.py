# -*- coding: utf-8 -*-
"""
ДОКТРИНА-КАК-КОД. Все выстраданные уроки отбора/ритма/цвета — в одном месте, как
проверяемые правила с порогами. Источник правды для validate_shots.py и сборки.
Меняешь урок -> меняешь ЗДЕСЬ (а не в голове ассистента). Ссылки — на справочники
скилла drone-edit/references/*.md.

Каждый порог прокомментирован причиной (чей это был провал). НЕ трогать пороги без
причины — они выстраданы итерациями «пользователь слышит/видит промах».
"""
import numpy as np

# ── Пороги окна кадра (selection-rules.md, stage4.best_window_of_length) ───────
D = {
    "jerk_max": 0.6,          # >0.6 = рывок скорости (в фильмах строже)
    "flow_dead": 0.6,         # flow.mean < 0.6 = реально мёртвый стоп-кадр (hard)
    "flow_static": 1.2,       # 0.6–1.2 = спокойный план (soft: ок, если композиция полная —
                              #           спокойные панорамы островов НЕ брак; пустоту ловит композиция)
    "startstop_ratio": 3.0,   # flow.max/flow.min > 3 = «стоит, потом летит»
    # ПОВОРОТ: плавный облёт героя — ПРИЁМ (не брак). Грубость ловим не по факту
    # вращения, а по РЕЗКОСТИ: рывок скорости (jerk) + угловое УСКОРЕНИЕ (скачок
    # скорости поворота). Плавный облёт = высокий curl + низкий jerk + ровная ω.
    # (Урок пользователя 2026-07-28: «облёт хороший, но резкие повороты грубо».)
    "curl_orbit": 0.9,        # curl > 0.9 = идёт вращение/облёт (сам по себе НЕ брак — инфо)
    "ang_accel_deg": 40.0,    # |Δ² угла| > 40°/с² = РЕЗКИЙ доворот (грубо) — вот это брак
    "ang_drift_deg": 120.0,   # суммарный дрейф угла (большой плавный пан — ок; инфо)
    "dead_entry_flow": 0.5,   # первый кадр окна < 0.5 = мёртвый вход (почти дисквал)
    "weak_entry_2s": 0.7,     # первые 2с < 0.7 = вялый вход
    "tail_spike_mult": 2.2,   # хвост > тела ×2.2 (и >3.0) = план «подлетает» в конце
    "min_shot_sec": 2.5,      # план должен «прожиться»; короче — только осознанный акцент
    "multi_part_gap_sec": 6.0,# из ОДНОГО дубля части разносить > 6с (иначе повтор)
    # ритм (rhythm-and-render.md)
    "bar_interval_lo": 1.90,  # интервал склеек В ТАКТАХ должен быть ~целый
    "bar_interval_hi": 2.10,
    "beat_dev_ms": 40.0,      # склейка в рендере от реальной доли — < полкадра (~16.7мс идеал, 40 порог)
    "onset_snap_sec": 0.12,   # снэп границы к сильнейшему онсету в ±0.12с
    # цвет (color.md) — не для гейта планов, для памяти/грейда
    "wb_align_strength": 0.15, "wb_corridor": (0.96, 1.04),   # ББ почти не трогать (закат зеленел)
    "sat_align_strength": 0.30, "sat_corridor": (0.86, 1.20), # насыщ мягко (закат душило)
    "exp_align_strength": 0.60,                               # выравнивать можно ТОЛЬКО экспозицию
}

DEFECT_FLAGS = ("пересвет", "провал", "расфокус", "мыло", "рывок")


def composition_flags(gray):
    """Композиция по кадру (метрики движения её НЕ ловят — урок 2026-07-28,
    'обрезанный корабль слева + пустая вода'). Возвращает [(rule, detail)].
    Идея: карта деталей (Лаплас). Пустой кадр = мало деталей. Объект у края =
    центр масс деталей уехал к краю, а противоположная половина пустая."""
    import cv2
    g = cv2.resize(gray, (180, 320))
    e = np.abs(cv2.Laplacian(g, cv2.CV_32F, ksize=3))
    thr = max(e.mean() + 0.4 * e.std(), 6.0)
    mask = e > thr
    frac = float(mask.mean())
    flags = []
    # НОЧЬ (партия Гонконг): тёмный кадр ≠ пустой — детали в огнях, Лаплас на ночи
    # физически низкий. empty_frame только для дневных кадров.
    if frac < 0.14 and float(g.mean()) > 45:
        flags.append(("empty_frame", f"деталей лишь {frac*100:.0f}% (пустой кадр: вода/небо)"))
        return flags
    ys, xs = np.nonzero(mask)
    H, W = g.shape
    cx, cy = xs.mean() / W, ys.mean() / H
    # доля деталей в противоположной от центра-масс половине по X
    left = float((xs < W / 2).mean())
    if cx < 0.34 or cx > 0.66:
        thin = left if cx > 0.66 else (1 - left)   # насколько пуста «дальняя» половина
        if thin < 0.22:
            flags.append(("edge_subject", f"объект прижат к краю (cx={cx:.2f}, дальняя половина пуста)"))
    return flags


def window_secs(clip, start, dur):
    """Посекундные метрики из clip['seconds'] на окне [start, start+dur)."""
    L = max(1, int(round(dur)))
    by = {s["t"]: s for s in clip.get("seconds", [])}
    return [by[t] for t in range(int(round(start)), int(round(start)) + L) if t in by]


def check_shot(clip, start, dur, *, is_open=False, forced=False, prev_idx=None,
               location_idx=None, person_idx=(), film=False, allow_spin=False):
    """Проверить ОДИН план по доктрине. Возвращает список нарушений:
    [{"rule","sev","detail"}], sev = 'hard' (не отдавать) | 'soft' (предупреждение).
    forced=True (контровой/солнце вручную) снимает штраф за недосвет — это ДОРОГОЙ
    кадр, детектор брака ложно метит силуэт как 'провал' (selection-rules.md)."""
    v = []
    idx = clip["idx"]; cd = clip["duration_sec"]

    # локация / человек / выход за длину — жёсткие
    if location_idx is not None and idx not in location_idx:
        v.append({"rule": "wrong_location", "sev": "hard", "detail": f"idx{idx} не из этой локации"})
    if idx in person_idx:
        v.append({"rule": "person_in_frame", "sev": "hard", "detail": f"idx{idx} — человек в кадре"})
    if start + dur > cd + 0.05:
        v.append({"rule": "window_overrun", "sev": "hard",
                  "detail": f"окно {start:.1f}+{dur:.1f} > клипа {cd:.1f} (заморозка tpad)"})
    if idx == prev_idx:
        v.append({"rule": "jump_cut", "sev": "hard", "detail": f"два окна idx{idx} подряд"})
    if dur < D["min_shot_sec"] - 0.05:
        v.append({"rule": "min_length", "sev": "soft",
                  "detail": f"{dur:.1f}с < {D['min_shot_sec']}с (ок только как акцент)"})

    win = window_secs(clip, start, dur)
    if not win:
        return v
    flows = [w["flow"] for w in win]
    f_min, f_max, f_mean = min(flows), max(flows), sum(flows) / len(flows)
    jerk_max = max(w.get("jerk", 0) for w in win)
    # РАЗВОРОТ НА МЕСТЕ (2026-08-19, шары Анатолии, ТРИ итерации жалоб «поворотные»):
    # равномерный yaw-пан детекторы резкости пропускали (он «плавный»), но пользователь:
    # «развороты на месте плохо смотрятся, особенно на фоне остальных». Метрика turn
    # (град/с посекундно): чистые пролёты <=4, развороты 11-65. Порог 10 = hard.
    # Разделитель «разворот на месте» vs «пан в полёте» — отношение turn/flow
    # посекундно: развороты 21-242 (вращение без поступательного движения), чистые
    # пролёты и одобренный мост (быстрый полёт с паном) <= 5.4. Порог 12.
    # окно +1с: пик вращения в ЧАСТИЧНОЙ последней секунде усечённого окна ускользал
    # (родовая причина вечных «доворотов в хвосте»). Порог turn>6: чистые <=4.6.
    import math as _m
    _win_ext = window_secs(clip, start, _m.ceil(start + dur) - start) or win
    _spin = [(w.get("turn") or 0) / max(w.get("flow") or 0, 0.15)
             for w in _win_ext if (w.get("turn") or 0) > 6]
    # allow_spin (curation orbits_ok, 2026-08-29): метрики НЕ отличают любимый ОБЛЁТ
    # (Халонг «вокруг горы-дракона», turn до 177 при низком curl) от брака-разворота —
    # различие в удержании объекта, это эстетика. Скоуп по партии, как subject_motion_idx.
    if _spin and max(_spin) > 12 and not allow_spin:
        v.append({"rule": "yaw_spot", "sev": "hard",
                  "detail": f"разворот на месте: turn/flow {max(_spin):.0f} > 12"})
    # РАЗГОН С МЕСТА В ЛЮБОМ СЛОТЕ (2026-08-19, «кадр стоит и только через секунду
    # начинает двигаться — это брак»): статичный первый такт при живом продолжении.
    # Раньше dead_entry ловился только на опенере рилса. Лечится сдвигом окна.
    if len(flows) >= 3 and flows[0] < 0.15 and f_max > 0.6:
        v.append({"rule": "dead_start", "sev": "hard",
                  "detail": f"разгон с места: старт flow {flows[0]:.2f} при движении дальше {f_max:.2f} — сдвинуть окно"})
    curl_max = max(w.get("curl", 0) for w in win)
    angs = [w.get("ang") for w in win if w.get("ang") is not None]
    # угловая скорость ω (град/с) и ускорение (град/с²): резкость поворота
    if len(angs) > 2:
        omega = np.degrees(np.diff(np.unwrap(angs)))         # град/с (посекундно)
        ang_accel = float(np.max(np.abs(np.diff(omega))))    # макс |Δω| = резкость
        drift = float(np.abs(omega).sum())
    else:
        ang_accel, drift = 0.0, 0.0

    if jerk_max > D["jerk_max"]:
        # ФОРС-окно (контровой/солнце, ручной выбор пользователя) — детектор мягче:
        # пользователь выбрал кусок осознанно; hard только при явной дёрганости (>0.9)
        sev = "soft" if (forced and jerk_max <= 0.9) else "hard"
        v.append({"rule": "jerk", "sev": sev, "detail": f"рывок скорости {jerk_max:.2f} > {D['jerk_max']}" + (" (форс-окно)" if sev == "soft" else "")})
    # СТАТИКА НЕ hard (урок 2026-08-01): пользователь ЛЮБИТ спокойные общие планы
    # (37/45/46/38 забракованы зря). Брак движения = рывок/резкий поворот. Пустоту
    # («ничего в кадре») ловит КОМПОЗИЦИЯ, не flow. Статика — только инфо.
    if f_mean < D["flow_static"]:
        v.append({"rule": "calm", "sev": "soft", "detail": f"flow.mean {f_mean:.2f} (спокойный — ок, пустоту ловит композиция)"})
    if f_max / max(0.05, f_min) > D["startstop_ratio"]:
        v.append({"rule": "start_stop", "sev": "soft", "detail": f"flow max/min {f_max/max(0.05,f_min):.1f} > {D['startstop_ratio']}"})
    # РЕЗКИЙ поворот: угл.ускорение по ПОСЕКУНДНОМУ ang (1 Гц) НЕНАДЁЖНО на коротком окне
    # (2-я производная 3-4 точек = шум) -> SOFT, НЕ hard. Урок 2026-08-02: idx29@1/45@13/40@25
    # ловились как sharp_turn, но пользователь одобрил. Надёжный hard-гейт грубости = JERK
    # (рывок скорости): ночной idx44 (jerk0.70)/жопа idx42-43 (jerk1.2-1.5) = грубо И по jerk.
    # TODO: точный детект поворота по ПОКАДРОВОМУ оптпотоку (curl/ang_accel per-frame), тогда hard.
    if ang_accel > D["ang_accel_deg"]:
        v.append({"rule": "sharp_turn", "sev": "soft",
                  "detail": f"возможный доворот, угл.ускорение {ang_accel:.0f}°/с² (1 Гц — грубая оценка)"})
    if curl_max > D["curl_orbit"]:
        v.append({"rule": "orbit", "sev": "soft",
                  "detail": f"облёт/вращение curl {curl_max:.2f} (ок если плавный: ускор. {ang_accel:.0f}°/с²)"})
    if drift > D["ang_drift_deg"]:
        v.append({"rule": "big_pan", "sev": "soft", "detail": f"большой пан {drift:.0f}° (ок если плавный)"})
    if win[0]["flow"] < D["dead_entry_flow"]:
        # hard только для ХУКА РИЛСА; в ФИЛЬМЕ спокойное открытие = правило 7 (не брак)
        v.append({"rule": "dead_entry", "sev": "hard" if (is_open and not film) else "soft",
                  "detail": f"вход flow {win[0]['flow']:.2f} < {D['dead_entry_flow']}"})
    elif len(win) >= 2 and (win[0]["flow"] + win[1]["flow"]) / 2 < D["weak_entry_2s"]:
        v.append({"rule": "weak_entry", "sev": "soft", "detail": "первые 2с вялые"})
    if len(win) >= 2:
        tail = win[-1]["flow"]; body = sum(w["flow"] for w in win[:-1]) / max(1, len(win) - 1)
        if tail > body * D["tail_spike_mult"] and tail > 3.0:
            v.append({"rule": "tail_spike", "sev": "soft", "detail": f"выход подлетает (хвост ×{tail/max(body,0.5):.1f})"})

    # брак по флагам (кроме недосвета на контровом/солнце — forced)
    for w in win:
        for f in w.get("flags", []) if isinstance(w.get("flags"), list) else []:
            pass
    for d in clip.get("defects", []):
        if d["t"] < start or d["t"] >= start + dur:
            continue
        for f in d.get("flags", []):
            if forced and ("провал" in f):   # контровой силуэт — ложный недосвет
                continue
            if any(k in f for k in DEFECT_FLAGS):
                v.append({"rule": "defect_flag", "sev": "soft", "detail": f"t{d['t']}: {f}"})
    return v
