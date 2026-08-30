# -*- coding: utf-8 -*-
"""
Пересборка рилса под музыкальную сетку (этап 3 -> этап 4).

В отличие от stage4_reels.py (ровные 4с без музыки), здесь склейки ставятся
на границы тактов реального трека (work/music_grid.json), длина плана —
переменная (из битовой сетки), но не короче min_clip_sec. Хук (правило 8) —
первый план. Аудио берётся из самого трека и мультиплексируется в превью.

Использование:
    python stage4_reels_music.py <reel_name> <track_file.mp3> [<track_file2.mp3> ...]
Пример:
    python stage4_reels_music.py reel01_ninhbinh_karst \
        "Tycho_-_Awake_44900069.mp3" "Ian_Asher_-_Take_Me_To_The_Moon_81645636.mp3"

Каждый трек даёт отдельный вариант: output/reels/<reel_name>__<track_stub>.*
"""
import os, sys, json, re, subprocess
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otio_export import build_timeline, export_timeline
from stage4_reels import signature, similarity, window_stats, hook_score, SIM_THRESHOLD, REELS
from vertical_crop import render_tracked_crop

BAR_OPTIONS = (1, 2, 4)  # варианты группировки тактов на один план (подбираем под темп)


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def slugify(track_file):
    stem = os.path.splitext(track_file)[0]
    stem = re.split(r"[_\-]", stem)[0:2]
    return re.sub(r"[^a-zA-Z0-9]+", "", "_".join(stem)).lower() or "track"


def snap_to_phrase_end(track_info, min_total_sec, max_total_sec):
    """Ищем конец музыкальной фразы (границу куплета/секции) рядом с желаемой длиной,
    чтобы рилс обрывался на явном музыкальном рубеже, а не посреди фразы."""
    ends = [p["end"] for p in track_info.get("phrases", [])]
    if not ends:
        return max_total_sec
    inside = [e for e in ends if min_total_sec <= e <= max_total_sec]
    if inside:
        return max(inside)  # предпочитаем более длинный вариант внутри диапазона
    below = [e for e in ends if e <= max_total_sec]
    if below:
        return max(below)
    return min(ends)


def dedouble_beats(track_info, factor):
    """Коррекция удвоенного (утроенного) темпа: прореживаем бит-сетку в `factor`
    раз, оставляя СИЛЬНУЮ подсетку (реальные доли, не синкопы между ними).
    librosa на медленных треках часто даёт tempo x2 -> «такт» вдвое короче
    реального, из-за чего build_shot_grid шагает 1.5 реального такта и склейки
    уезжают на бэкбит. См. music.dedouble в config.yaml. Онсеты не трогаем —
    снэп к транзиенту остаётся точным."""
    beats = np.array(track_info.get("beats") or [], dtype=float)
    if factor < 2 or len(beats) < factor * 2:
        return track_info
    onsets = track_info.get("onsets") or []
    if onsets:
        ot = np.array([o[0] for o in onsets], dtype=float)
        os_ = np.array([o[1] for o in onsets], dtype=float)
        def bstr(bt):
            m = np.abs(ot - bt) <= 0.08
            return float(os_[m].max()) if m.any() else 0.0
        strength = np.array([bstr(b) for b in beats])
        phase = max(range(factor), key=lambda p: strength[p::factor].sum())
    else:
        phase = 0
    info2 = dict(track_info)
    kept = beats[phase::factor]
    info2["beats"] = [round(float(b), 3) for b in kept]
    if len(kept) > 2:
        info2["tempo_bpm"] = round(60.0 / float(np.median(np.diff(kept))), 1)
    return info2


def build_shot_grid(track_info, beats_per_bar, target_shot_sec, min_clip_sec, max_total_sec,
                    music_start=0.0, force_onset_grid=False):
    """Сетка склеек ПО ФРАЗАМ (правило 5): режем на ГРАНИЦАХ ТАКТОВ на СИЛЬНУЮ
    ДОЛЮ (даунбит), интервалы — ЦЕЛОЕ число тактов. Возвращает (t_start, dur) от
    начала трека.

    ПОЧЕМУ не «ближайший сильный онсет к t+4с» (старый способ): цель 4.0с ≈ 1.96
    такта — НЕ кратно; снэп к любому сильному онсету регулярно попадал на слабую
    долю (бит 2/3) и давал интервалы 2.21 / 1.47 такта -> склейка уезжала с
    даунбита, монтаж «плыл» и «запаздывал» (пользователь ловил на 0:48, 2:32 три
    раза подряд). Правильно: шагать РОВНО по N тактов от даунбита, а к КАДРУ
    склейку доводить снэпом на СИЛЬНЕЙШИЙ онсет у самого даунбита (точный
    транзиент, но не сходя с доли).

    music_start: с какой секунды трека начинать.
    """
    all_beats = np.array(track_info.get("beats") or [], dtype=float)
    onsets = track_info.get("onsets") or []
    FPS = 29.97
    q = lambda x: round(x * FPS) / FPS

    # запасной путь (нет битов/онсетов) ИЛИ --onset-grid: шаг по СИЛЬНЫМ ОНСЕТАМ.
    # Нужен, когда жёсткая тактовая сетка (median BPM) уплывает от реального пульса
    # и склейки попадают в «пустоту» между ударами (Ian Asher 117.5: −365мс на 4-6с).
    # Тут каждая склейка лип­нет к реальному сильному транзиенту -> всё на ударе.
    if force_onset_grid or len(all_beats) < beats_per_bar * 2 or not onsets:
        strong = np.array([b for b in (track_info.get("strong_beats") or all_beats)
                           if b >= music_start - 0.05], dtype=float)
        if len(strong) < 3:
            return []
        bi = float(np.median(np.diff(all_beats))) if len(all_beats) > 2 else 0.5
        target = max(min_clip_sec, target_shot_sec)
        shots, total, t = [], 0.0, q(float(strong[0]))
        while True:
            cand = strong[strong >= t + min_clip_sec]
            if not len(cand):
                break
            nxt = q(float(cand[np.argmin(np.abs(cand - (t + target)))]))
            if total + (nxt - t) > max_total_sec:
                break
            shots.append({"t": t, "dur": nxt - t}); total += nxt - t; t = nxt
        return shots

    on_t = np.array([o[0] for o in onsets], dtype=float)
    on_s = np.array([o[1] for o in onsets], dtype=float)
    beat_interval = float(np.median(np.diff(all_beats)))
    bar = beats_per_bar * beat_interval

    # тактов на план — ЦЕЛОЕ, ближе к target, но не короче min_clip
    n_bars = max(1, int(round(target_shot_sec / bar)))
    while n_bars * bar < min_clip_sec - 1e-6:
        n_bars += 1
    step = n_bars * beats_per_bar               # шаг сетки в долях

    # сила каждого бита = сильнейший онсет в ±80мс от него
    def beat_strength(bt):
        m = np.abs(on_t - bt) <= 0.08
        return float(on_s[m].max()) if m.any() else 0.0
    bstr = np.array([beat_strength(b) for b in all_beats])

    # ФАЗА фразовой сетки: сдвиг, при котором на границах максимум акцента
    # (так границы фраз попадают на реальные ДАУНБИТЫ, а не на слабые доли)
    best_off, best_sc = 0, -1.0
    for off in range(step):
        sc = float(bstr[off::step].sum())
        if sc > best_sc:
            best_sc, best_off = sc, off
    bound_idx = list(range(best_off, len(all_beats), step))
    bounds = [all_beats[i] for i in bound_idx if all_beats[i] >= music_start - 0.05]
    if len(bounds) < 2:
        return []

    # снэп границы такта к СИЛЬНЕЙШЕМУ онсету в ±0.12с (точный транзиент даунбита)
    def snap(bt):
        m = np.abs(on_t - bt) <= 0.12
        if m.any():
            idx = np.where(m)[0]
            return q(float(on_t[idx[np.argmax(on_s[idx])]]))
        return q(float(bt))
    snapped = [snap(b) for b in bounds]
    # монотонность (снэп не должен схлопнуть соседние границы)
    for k in range(1, len(snapped)):
        if snapped[k] <= snapped[k - 1] + min_clip_sec - 0.2:
            snapped[k] = q(float(bounds[k]))

    shots, total = [], 0.0
    for k in range(len(snapped) - 1):
        t, nxt = snapped[k], snapped[k + 1]
        d = nxt - t
        if d < min_clip_sec - 1e-6:
            continue
        if total + d > max_total_sec:
            break
        shots.append({"t": t, "dur": d})
        total += d
    return shots


def build_segment_pool(catalog, cand_idx, exclude_specs, landscape=False):
    """Пул кандидатов — не клип целиком, а отдельный ЧИСТЫЙ СЕГМЕНТ внутри клипа
    (этап 2 v2: multi-segment + hero/filler, см. reels-pipeline память). Один
    длинный дубль теперь может дать несколько разных планов в отбор — не только
    самое короткое лучшее окно на весь файл.

    Ключ кандидата — "idx.segment_index" (напр. "21.0", "21.1"); exclude_specs
    принимает и голый idx (все сегменты клипа), и конкретный "idx.seg".

    Горизонтальные клипы ТОЖЕ идут в пул (needs_crop=True) — правило 9: кроп
    16:9->9:16 с трекингом точки интереса, не по центру (см. vertical_crop.py).
    Без этого главы с преобладанием горизонтали (напр. Халонг) теряли материал."""
    pool = []
    for c in catalog["clips"]:
        if c["idx"] not in cand_idx:
            continue
        for si, seg in enumerate(c.get("segments", [])):
            key = f"{c['idx']}.{si}"
            if any(spec == str(c["idx"]) or spec == key for spec in exclude_specs):
                continue
            # needs_crop: в вертикальном режиме (9:16) кропим ГОРИЗОНТАЛЬНЫЕ клипы
            # (трекинг-кроп 16:9->9:16, правило 9). В landscape-режиме (16:9-фильм)
            # наоборот — горизонтали идут НАТИВНО без кропа, а кроп нужен лишь
            # вертикальным клипам (их в 16:9-специях обычно исключают).
            needs_crop = c["vertical"] if landscape else (not c["vertical"])
            pool.append({"key": key, "idx": c["idx"], "path": c["path"],
                        "file": c["file"], "seg": seg, "clip": c,
                        "needs_crop": needs_crop})
    return pool


def best_window_of_length(item, length, smooth_weight=1.0):
    """Лучшее окно произвольной длины ТОЛЬКО в границах own-сегмента.

    ВАЖНО: окно выбирается не только по красоте (sec_scores), но и по ПЛАВНОСТИ
    движения. Иначе внутри хорошего сегмента берётся кусок с рывком или
    старт-стопом («камера стоит, потом летит») — именно из-за этого рилс 4 в
    первой сборке вышел «грязным»: сегмент годный, а окно внутри — нет.
    Штрафуем рывки (jerk) и разброс скорости движения внутри окна.
    """
    clip, seg = item["clip"], item["seg"]
    scores_all = clip.get("sec_scores") or []
    secs = clip.get("seconds") or []
    # hi КЛАМПИМ к реальной длине файла: сегменты stage2 иногда вылезают за длину
    # (напр. seg.end=22 при файле 21.35с) -> окно уходит в несуществующий материал,
    # клип рендерится короче слота и сбивает бит у всего после. Берём floor(dur),
    # чтобы последняя целая секунда точно существовала.
    clip_dur = clip.get("duration_sec")
    hi = int(seg["end"])
    if clip_dur:
        hi = min(hi, int(clip_dur))
    lo = min(int(seg["start"]), max(0, hi - 1))
    scores = scores_all[lo:hi]
    n = len(scores)
    L = max(1, int(round(length)))
    if n == 0:
        return {"start": float(lo), "score": seg.get("score", 0.5)}
    if n <= L:
        return {"start": float(lo), "score": float(np.mean(scores))}

    by_t = {s["t"]: s for s in secs}
    best_off, best_val, best_raw = 0, -1e9, 0.0
    for off in range(0, n - L + 1):
        raw = float(np.mean(scores[off:off + L]))
        win = [by_t.get(lo + off + k) for k in range(L)]
        win = [x for x in win if x]
        penalty = 0.0
        if win:
            jerk_max = max(x["jerk"] for x in win)
            flows = [x["flow"] for x in win]
            f_min, f_max = min(flows), max(flows)
            f_mean = sum(flows) / len(flows)
            penalty += min(1.0, jerk_max / 1.2)                       # рывок
            if f_max / max(0.05, f_min) > 3.0:
                penalty += 0.5                                        # старт-стоп
            # СТАТИКА: без этого штрафа фильтр выбирает самое «спокойное» окно,
            # где камера просто стоит (рывков нет — значит идеально), и план
            # выглядит стоп-кадром. Пользователь: «камера то стоит на месте».
            if f_mean < 1.2:
                penalty += min(0.8, (1.2 - f_mean) / 1.2 * 0.8)
            # РАЗГОН С МЕСТА / статичный ВХОД: план стоит первые секунды, ПОТОМ
            # летит. Это ОЧЕНЬ заметно, особенно в открывающем кадре (пользователь
            # дважды: рилс 5 «на 5с стоит», HK-фильм «первые 3 секунды стоит»).
            # Штраф почти дисквалифицирующий (1.5): такое окно не должно
            # выбираться, пока есть хоть какое-то с живым входом. Смотрим ПЕРВЫЕ
            # ДВЕ секунды окна — если они статичны, вход мёртвый.
            if len(win) >= 2:
                head2 = (win[0]["flow"] + win[1]["flow"]) / 2 if len(win) >= 2 else win[0]["flow"]
                if win[0]["flow"] < 0.5:
                    penalty += 1.5                    # первый кадр стоит — вход мёртвый
                elif head2 < 0.7:
                    penalty += 0.8                    # первые 2с вялые
                # ТОЧКА ВЫХОДА: последняя секунда — резкий всплеск скорости
                # (камера «подлетает вверх»/дёргается в конце куска, пользователь
                # на 2:36). Плохой out-point. Штрафуем спайк на конце окна.
                tail = win[-1]["flow"]
                body = sum(x["flow"] for x in win[:-1]) / max(1, len(win) - 1)
                if tail > body * 2.2 and tail > 3.0:
                    # штраф ПРОПОРЦИОНАЛЕН силе спайка: хвост ×2 к телу — мягко,
                    # ×4-5 (камера резко подлетает вверх на самом стыке) — почти
                    # дисквалификация. Фикс. +0.7 был слишком слаб: клип [34.0]
                    # (tail 10.1 при body 2.1, ×4.7) всё равно проходил в тонком
                    # слоте, и пользователь ловил дёрганый out-point на 2:36.
                    penalty += min(1.6, (tail / max(body, 0.5) - 1.0) * 0.5)
            # ПОВОРОТ/ОРБИТА камеры (вывод 2026-07-20): микродовороты и вращение
            # top-down бросаются в глаза в монтаже, а рывок их НЕ ловит (скорость
            # ровная, меняется НАПРАВЛЕНИЕ). Чистые «дорогие» кадры —
            # поступательные (наезд/пролёт/подъём). curl = вращение поля,
            # дрейф среднего угла потока по окну = доворот. Метрики из этапа 2 v3;
            # у старых клипов их нет -> .get(...,0), штраф просто не сработает.
            curl_max = max(x.get("curl", 0.0) for x in win)
            angs = [x.get("ang") for x in win if x.get("ang") is not None]
            drift = 0.0
            if len(angs) > 1:
                drift = float(np.degrees(np.abs(np.diff(np.unwrap(angs))).sum()))
            penalty += min(1.3, curl_max / 0.9)                       # орбита/крен (строже)
            penalty += min(1.0, drift / 55.0)                         # доворот (yaw, строже)
        val = raw - smooth_weight * penalty
        if val > best_val:
            best_val, best_off, best_raw = val, off, raw
    return {"start": float(lo + best_off), "score": best_raw}


def pick_music_synced_segments(cfg, catalog, cand_idx, shots, min_clip_sec,
                               exclude_specs=None, prefer_specs=None,
                               extend_map=None, window_map=None, speed_map=None,
                               open_with=None, landscape=False):
    """Слот 0 = хук (макс. hook_score), остальные — по возрастанию скора сегмента
    (нарастание к финалу рилса, hero-сегменты — с небольшим бонусом), с дедупом
    по визуальному сюжету.

    exclude_specs / prefer_specs: строки "idx" (весь клип) или "idx.seg"
    (конкретный сегмент) — ручная правка после разбора (см. reels-pipeline).

    extend_map: {"key": N} — план занимает N слотов сетки вместо одного (длинный
    план на музыкальной сетке; напр. финал-раскрытие). Слоты подряд «съедаются».
    window_map: {"key": src_in} — явное начало окна в исходнике, В ОБХОД границ
    сегмента и детектора брака. Нужно, когда детектор ошибся (напр. контровой
    закатный силуэт помечается как «недосвет», хотя это художественный кадр)."""
    exclude_specs = set(exclude_specs or ())
    prefer_specs = list(prefer_specs or ())
    extend_map = extend_map or {}
    window_map = window_map or {}
    speed_map = speed_map or {}
    ffmpeg = cfg["tools"]["ffmpeg"]
    pool_all = build_segment_pool(catalog, cand_idx, exclude_specs, landscape=landscape)
    if not pool_all or not shots:
        return []

    def fits(item, dur):
        return item["seg"]["dur"] >= dur - 0.2

    def spec_matches(spec, item):
        return spec == str(item["idx"]) or spec == item["key"]

    # ПРЕДФИЛЬТР ПУЛА: у сегмента должно быть хоть одно ЧИСТОЕ окно (без поворота/
    # статики/рывка). Иначе авто-заполнение хвоста тянет поворотный/статичный junk
    # (пользователь ловил это многократно). Форсированные (prefer/window/open/end)
    # оставляем всегда — там редактор проверил глазами.
    forced_keys = set(prefer_specs) | set(window_map) | {open_with} if open_with else \
        set(prefer_specs) | set(window_map)

    def has_clean_window(item, L=3):
        clip, seg = item["clip"], item["seg"]
        secs = {s["t"]: s for s in (clip.get("seconds") or [])}
        lo, hi = int(seg["start"]), int(seg["end"])
        for s in range(lo, max(lo + 1, hi - L + 1)):
            w = [secs.get(s + k) for k in range(L) if secs.get(s + k)]
            if len(w) < L - 1:
                continue
            jk = max(x["jerk"] for x in w)
            fl = np.mean([x["flow"] for x in w])
            cu = max(x.get("curl", 0) for x in w)
            angs = [x.get("ang") for x in w if x.get("ang") is not None]
            dr = np.degrees(np.abs(np.diff(np.unwrap(angs))).sum()) if len(angs) > 1 else 0
            head_ok = w[0]["flow"] >= 0.5
            if jk <= 0.6 and fl >= 1.0 and cu <= 0.8 and dr <= 55 and head_ok:
                return True
        return False

    def is_forced(item):
        return item["key"] in forced_keys or str(item["idx"]) in forced_keys
    pool_all = [it for it in pool_all if is_forced(it) or has_clean_window(it)]

    # --- слот 0 ---
    # рилсы: ХУК (макс. hook_score) первым (правило 8).
    # длинный ролик: спокойное ОТКРЫТИЕ (правило 7 — эффектное в финал, не в
    # начало) — задаётся open_with="idx.seg", хук-логика пропускается.
    d0 = shots[0]["dur"]
    chosen, sigs, used = [], [], set()
    if open_with:
        it = next((x for x in pool_all if spec_matches(open_with, x)), None)
        if it is not None:
            # --window "key:src_in" форсирует окно и для опенера (иначе брался
            # best_window и игнорировал ручной выбор старта — напр. поймать пуш/
            # засвет вместо статичного начала).
            forced_in = window_map.get(it["key"])
            if forced_in is not None:
                start, score = float(forced_in), it["seg"]["score"]
            else:
                w = best_window_of_length(it, d0)
                start, score = w["start"], w["score"]
            sig = signature(ffmpeg, it["path"], start + d0 / 2)
            if sig is not None:
                chosen.append({**it, "start": start, "score": score,
                               "dur": d0, "sig": sig})
                sigs.append(sig); used.add(it["key"])
    if not chosen:
        cand0 = []
        for it in pool_all:
            if not fits(it, d0):
                continue
            w = best_window_of_length(it, d0)
            ws = window_stats(it["clip"], w["start"], d0)
            cand0.append({**it, "start": w["start"], "score": w["score"], "ws": ws,
                          "hook": hook_score(ws)})
        cand0.sort(key=lambda x: -x["hook"])
        for cand in cand0:
            sig = signature(ffmpeg, cand["path"], cand["start"] + d0 / 2)
            if sig is None:
                continue
            cand["sig"] = sig
            chosen.append({**cand, "dur": d0})
            sigs.append(sig); used.add(cand["key"])
            break
    if not chosen:
        return []

    # --- остальные слоты: пул отсортирован по возрастанию скора сегмента
    # (build to finale), hero — небольшой бонус; prefer_specs — в начало очереди ---
    remaining = [it for it in pool_all if it["key"] not in used]
    preferred, seen_pref = [], set()
    for spec in prefer_specs:
        for it in remaining:
            if spec_matches(spec, it) and it["key"] not in seen_pref:
                preferred.append(it); seen_pref.add(it["key"])
    rest_of_pool = sorted(
        [it for it in remaining if it["key"] not in seen_pref],
        key=lambda it: it["seg"]["score"] + (0.03 if it["seg"]["bucket"] == "hero" else 0))
    rest_pool = preferred + rest_of_pool

    si = 1                       # индекс слота музыкальной сетки
    while si < len(shots):
        picked = None
        prev_idx = chosen[-1]["idx"] if chosen else None
        # два прохода: сперва ИЗБЕГАЕМ план из того же клипа, что предыдущий
        # (два окна одного водопада подряд = джамп-кат «дёргается», пользователь
        # на Вьетнам-фильме 2:43); если альтернатив нет — второй проход без этого.
        for relax_same_clip in (False, True):
            j = 0
            while j < len(rest_pool):
                it = rest_pool[j]; j += 1
                if it["key"] in used:
                    continue
                if (not relax_same_clip and prev_idx is not None
                        and it["idx"] == prev_idx):
                    continue
                n_slots = max(1, int(extend_map.get(it["key"], 1)))
                n_slots = min(n_slots, len(shots) - si)   # не вылезаем за сетку
                dur = sum(shots[si + k]["dur"] for k in range(n_slots))
                speed = float(speed_map.get(it["key"], 1.0))
                need_src = dur * speed
                forced_in = window_map.get(it["key"])
                if forced_in is None:
                    if not fits(it, need_src):
                        continue
                    w = best_window_of_length(it, need_src)
                    start = w["start"]; score = w["score"]
                else:
                    clip_dur = it["clip"].get("duration_sec", 0)
                    if forced_in + need_src > clip_dur + 0.2:
                        continue
                    start = float(forced_in); score = it["seg"]["score"]
                sig = signature(ffmpeg, it["path"], start + dur / 2)
                if sig is None:
                    continue
                if it["key"] not in seen_pref and any(
                        similarity(sig, ps) >= SIM_THRESHOLD for ps in sigs):
                    continue
                picked = {**it, "start": start, "score": score, "dur": dur,
                          "sig": sig, "slots": n_slots}
                break
            if picked is not None:
                break
        if picked is None:
            break  # пул исчерпан (дедуп) — заканчиваем рилс тут, короче — не страшно
        chosen.append(picked); sigs.append(picked["sig"]); used.add(picked["key"])
        si += picked["slots"]

    if len(chosen) < 2:
        return []

    segs = []
    for j, s in enumerate(chosen):
        hero_tag = ", hero" if s["seg"]["bucket"] == "hero" else ""
        crop_tag = ", crop" if s.get("needs_crop") else ""
        slots = s.get("slots", 1)
        slot_tag = f", {slots} слота" if slots > 1 else ""
        segs.append({
            "path": s["path"], "src_in": s["start"],
            "src_out": s["start"] + max(min_clip_sec, s["dur"]),
            "name": f"[{s['key']}] {s['file']} (score {s['score']:.2f}{hero_tag}{crop_tag}{slot_tag})"
                    + (" ХУК" if j == 0 else ""),
            "idx": s["idx"], "key": s["key"], "needs_crop": s.get("needs_crop", False),
        })
    return segs


def apply_speedup(catalog, segs, keys, factor):
    """Ускоряет выбранные планы (по ключу "idx.seg"): для ПРЕВЬЮ берём больше
    исходника в границах того же сегмента и сжимаем по времени (setpts) — план
    играет быстрее, но занимает тот же слот в музыкальной сетке. Не полный
    спид-рамп (slow-fast-slow) — по разбору с пользователем надёжнее и не
    рискует выглядеть гиммиково при частом использовании.

    src_in/src_out (и, соответственно, длина в OTIO/EDL для Resolve) НЕ
    трогаются — расширенное окно живёт отдельно в render_in/render_out и
    участвует только в рендере превью. Ретайм в самом Resolve делаете вручную
    (EDL всё равно ненадёжно переносит такие эффекты как метаданные)."""
    by_idx = {c["idx"]: c for c in catalog["clips"]}
    for s in segs:
        # keys может быть множеством (общий factor) или словарём key->factor
        if isinstance(keys, dict):
            if s["key"] not in keys:
                continue
            f = float(keys[s["key"]])
        else:
            if s["key"] not in keys:
                continue
            f = float(factor)
        clip = by_idx.get(s["idx"])
        if not clip:
            continue
        seg_i = int(s["key"].split(".")[1])
        seg = clip["segments"][seg_i]
        clip_dur = clip.get("duration_sec", 0)
        cur_dur = s["src_out"] - s["src_in"]
        need = cur_dur * f
        extra = need - cur_dur
        r_in, r_out = s["src_in"], s["src_out"]
        if f < 1.0:
            # замедление: нужно МЕНЬШЕ исходника — просто ужимаем окно с конца
            r_out = r_in + need
        elif r_out + extra <= min(seg["end"], clip_dur):
            r_out += extra
        elif r_in - extra >= seg["start"]:
            r_in -= extra
        else:
            continue  # не хватает запаса — оставляем как есть
        s["render_in"], s["render_out"] = r_in, r_out
        s["speed"] = f
        s["name"] += f" [x{f:.2f}]"
    return segs


def apply_zoom(segs, keys, amount):
    """Лёгкий punch-in (сужение кропа за окно) для почти статичных горизонтальных
    планов — даёт ощущение движения там, где его особенно не хватает (см. разбор:
    естественный лёгкий снос дрона на статичной композиции читается как
    "дрожание", хотя фазовая корреляция не находит реальной тряски камеры)."""
    for s in segs:
        if s["key"] in keys:
            s["zoom"] = amount
            s["name"] += f" [zoom{amount:.2f}]"
    return segs


def render_preview_with_audio(cfg, segs, audio_path, audio_start, total_dur, out_mp4,
                              fade=0.6, res=(1080, 1920), crf=18, preset="fast"):
    ffmpeg = cfg["tools"]["ffmpeg"]
    w, h = res
    crop_dir = os.path.join(ROOT, "work", "tracked_crops")

    # горизонтальные планы (needs_crop) — трекинг-кроп 16:9->9:16 отдельным
    # проходом ПЕРЕД основной сборкой (правило 9). Работаем с копией полей
    # (не трогаем segs) — OTIO/EDL для Resolve ссылается на исходный файл,
    # кроп с трекингом там не выразить надёжно как метаданные (см. память).
    render_specs = []
    for s in segs:
        r_in, r_out, path = s.get("render_in", s["src_in"]), s.get("render_out", s["src_out"]), s["path"]
        if s.get("needs_crop"):
            safe_key = s["key"].replace(".", "_")
            tmp_path = os.path.join(crop_dir, f"{safe_key}.mp4")
            track_path = os.path.join(crop_dir, f"{safe_key}_track.csv")
            got = render_tracked_crop(cfg, path, r_in, r_out - r_in, w, h, tmp_path,
                                      save_track_as=track_path, zoom=s.get("zoom", 0.0))
            if got:
                path, r_in, r_out = got, 0.0, (r_out - r_in)
            else:
                print(f"  кроп-трекинг не удался для {s['name']} — беру как есть")
        render_specs.append({**s, "path": path, "render_in": r_in, "render_out": r_out})

    FR = 30000 / 1001
    inputs, filters, labels = [], [], []
    for i, s in enumerate(render_specs):
        r_in, r_out = s["render_in"], s["render_out"]
        # ТОЧНОЕ число кадров слота из ДЛИТЕЛЬНОСТИ ТАЙМЛАЙНА (src_out-src_in),
        # квантованной гридом к кадру. Режем каждый клип по кадрам (trim=end_frame),
        # а не по времени: иначе клип рендерится на ~1 кадр длиннее и ошибка КОПИТСЯ
        # -> картинка отстаёт от бита (пользователь: «после 50с переходы позже»).
        disp_dur = s["src_out"] - s["src_in"]
        nf = max(1, int(round(disp_dur * FR)))
        # запас на входе (+0.15с), чтобы было ИЗ ЧЕГО обрезать ровно nf кадров
        inputs += ["-ss", f"{r_in:.3f}", "-t", f"{r_out - r_in + 0.15:.3f}", "-i", s["path"]]
        speed = s.get("speed", 1.0)
        geom = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
        if speed != 1.0:
            # РЕТАЙМ ТОЛЬКО ЧЕРЕЗ minterpolate. Простой setpts+fps при дробном
            # коэффициенте выбрасывает/дублирует кадры неравномерно -> джаддер,
            # который читается как «дрожание картинки» (измерено: разброс
            # каденции 0.50-9.25 против 0.16 у неретаймленного плана; на
            # кроп-планах до 34% дублей). minterpolate синтезирует кадры по
            # движению -> каденция 0.16, как у чистого плана.
            # fps — ТОЧНЫЙ NTSC 30000/1001, а не «29.97»: округлённое 29.97 даёт
            # растяжение времени ~0.25% -> картинка прогрессивно отстаёт от бита
            # (пользователь: «после 50с каждый переход на 1-2с позже»). См. ниже.
            filters.append(f"[{i}:v]{geom},setpts=PTS/{speed},"
                           f"minterpolate=fps=30000/1001:mi_mode=mci:mc_mode=aobmc:vsbmc=1,"
                           f"tpad=stop_mode=clone:stop_duration=3,"
                           f"trim=end_frame={nf},setpts=PTS-STARTPTS[v{i}]")
        else:
            # tpad перед trim ГАРАНТИРУЕТ ровно nf кадров: если у клипа не хватает
            # материала (сегмент stage2 вылез за длину файла), клонируем последний
            # кадр до нужной длины. Без этого короткий клип рендерится КОРОЧЕ слота
            # -> все склейки ПОСЛЕ него уезжают с бита (idx107 DJI_0999: сегмент до
            # 22с при файле 21.35с -> -667мс -> весь фильм после 1:24 мимо бита).
            filters.append(f"[{i}:v]{geom},fps=30000/1001,"
                           f"tpad=stop_mode=clone:stop_duration=3,"
                           f"trim=end_frame={nf},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
    # ПОСЛЕ concat принудительно нормализуем к строгому CFR 30000/1001 и
    # перегенерируем PTS от НОМЕРА КАДРА (setpts=N/FR/TB). Без этого concat копит
    # микросдвиги PTS по клипам -> avg_frame_rate уезжает (замерено 29.8956 вместо
    # 29.97) и склейки прогрессивно отстают от аудио. N/FR/TB кладёт кадр N ровно
    # на N*1001/30000 c -> видео и звук залочены намертво.
    fc = (";".join(filters) + ";" + "".join(labels)
          + f"concat=n={len(render_specs)}:v=1:a=0[vcat];"
          + "[vcat]fps=30000/1001,setpts=N/(30000/1001)/TB[vraw]")
    # длинный ролик заканчиваем ДЛИННЫМ затуханием музыки и картинки — «как
    # оркестр закончил» (пользователь). Рилс — короткий fade. `fade` передаётся
    # из main (для фильмов ~2.5с).
    fade_dur = min(fade, max(0.2, total_dur * 0.35))
    fade_start = max(0.0, total_dur - fade_dur)
    # fade-out и на видео (было только на звуке — обрыв картинки резал глаз так же, как звук)
    fc += f";[vraw]fade=t=out:st={fade_start:.3f}:d={fade_dur:.3f}[out]"
    a_idx = len(render_specs)
    inputs += ["-ss", f"{audio_start:.3f}", "-t", f"{total_dur:.3f}", "-i", audio_path]
    fc += f";[{a_idx}:a]afade=t=out:st={fade_start:.3f}:d={fade_dur:.3f}[aout]"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
        "-filter_complex", fc, "-map", "[out]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        # строгий CFR на выходе: держим кадры на точной NTSC-сетке (иначе
        # контейнер пишет avg_fps<29.97 и картинка отстаёт от звука)
        "-r", "30000/1001", "-fps_mode", "cfr", "-video_track_timescale", "30000",
        "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ffmpeg ERROR:", (r.stderr or "")[-800:])
        return False
    return True


def _parse_spec_list(args, flag):
    """--exclude/--prefer/--end-with принимают "idx" (весь клип) или "idx.seg"
    (конкретный сегмент, см. build_segment_pool) — строки, не числа."""
    if flag in args:
        i = args.index(flag)
        vals = args[i + 1]
        del args[i:i + 2]
        return [x.strip() for x in vals.split(",") if x.strip()]
    return []


def _parse_kv_map(args, flag, n_vals=1, cast=float):
    """--extend "27.0:2,28.2:3" -> {"27.0": 2.0, ...}
       --window "28.2:10.5"     -> {"28.2": 10.5}"""
    out = {}
    if flag in args:
        i = args.index(flag)
        vals = args[i + 1]
        del args[i:i + 2]
        for item in vals.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            out[parts[0]] = cast(parts[1]) if len(parts) > 1 else None
    return out


def _parse_float(args, flag, default):
    if flag in args:
        i = args.index(flag)
        val = float(args[i + 1])
        del args[i:i + 2]
        return val
    return default


def main():
    args = sys.argv[1:]
    exclude_specs = _parse_spec_list(args, "--exclude")
    prefer_specs = _parse_spec_list(args, "--prefer")
    end_with = _parse_spec_list(args, "--end-with")
    speedup_keys = set(_parse_spec_list(args, "--speedup-keys"))
    speedup_factor = _parse_float(args, "--speedup-factor", 1.6)
    zoom_keys = set(_parse_spec_list(args, "--zoom-keys"))
    zoom_amount = _parse_float(args, "--zoom-amount", 0.12)
    end_fade = _parse_float(args, "--end-fade", 0.6)   # длинное затухание для фильмов (~2.5)
    extend_map = _parse_kv_map(args, "--extend", cast=float)
    window_map = _parse_kv_map(args, "--window", cast=float)
    speed_map = _parse_kv_map(args, "--speed", cast=float)   # key:factor (>1 быстрее, <1 медленнее)
    max_sec_override = _parse_float(args, "--max-sec", None)
    min_clip_override = _parse_float(args, "--min-clip", None)  # осознанно короче 2.5 (правило 6)
    clip_len_override = _parse_float(args, "--clip-len", None)  # переопределить длину плана спеки
    music_start = _parse_float(args, "--music-start", 0.0)
    onset_grid = "--onset-grid" in args   # склейки по сильным онсетам (не жёсткий такт)
    if onset_grid:
        args.remove("--onset-grid")
    open_with_list = _parse_spec_list(args, "--open-with")
    open_with = open_with_list[0] if open_with_list else None
    if len(args) < 2:
        print(__doc__); return
    reel_name, track_files = args[0], args[1:]

    cfg = load_cfg()
    with open(os.path.join(ROOT, "work", "clip_catalog.json"), "r", encoding="utf-8") as f:
        catalog = json.load(f)
    grid_path = os.path.join(ROOT, "work", "music_grid.json")
    with open(grid_path, "r", encoding="utf-8") as f:
        grid = json.load(f)

    spec = next((r for r in REELS if r["name"] == reel_name), None)
    if spec is None:
        print(f"Неизвестный рилс: {reel_name}"); return

    # 16:9-фильм vs вертикальный рилс/фильм — задаётся полем "aspect":"16:9" в спеке.
    # Landscape: рендер 1920x1080, горизонтали БЕЗ кропа, длинный ролик (правило 7).
    landscape = spec.get("aspect") == "16:9"
    render_res = (1920, 1080) if landscape else (1080, 1920)

    min_clip = min_clip_override if min_clip_override is not None else cfg["edit"]["min_clip_sec"]
    max_total = max_sec_override if max_sec_override is not None else cfg["edit"]["reels"]["max_sec"]
    target_shot = clip_len_override if clip_len_override is not None else spec["clip_len"]
    beats_per_bar = grid["beats_per_bar"]
    music_dir = os.path.join(ROOT, cfg["paths"]["music"])
    out_dir = os.path.join(ROOT, "output", "reels")

    for track_file in track_files:
        info = grid["tracks"].get(track_file)
        print("=" * 70)
        print(f"РИЛС: {spec['title']}  x  {track_file}")
        if info is None:
            print("  Нет в music_grid.json — сначала прогони stage3_music.py"); continue

        # Коррекция удвоенного темпа (music.dedouble в config): прореживаем
        # бит-сетку, чтобы такт стал реальным и склейки шли по даунбитам.
        dd_factor = int((cfg.get("music", {}).get("dedouble") or {}).get(track_file, 1))
        if dd_factor >= 2:
            bpm_before = info.get("tempo_bpm")
            info = dedouble_beats(info, dd_factor)
            print(f"  коррекция октавы темпа /{dd_factor}: {bpm_before} -> "
                  f"{info.get('tempo_bpm')} BPM (реальный такт, склейки на даунбит)")

        # --music-start ПРИЛИПАЕТ к началу ближайшей музыкальной фразы: иначе
        # вход в трек посреди куплета звучит резко/странно (пользователь на
        # рилсе 5). Фразы = музыкальные секции из music_grid.
        eff_music_start = music_start
        if music_start > 0.5 and info.get("phrases"):
            starts = [p["start"] for p in info["phrases"]]
            eff_music_start = min(starts, key=lambda s: abs(s - music_start))
            if abs(eff_music_start - music_start) > 0.1:
                print(f"  music-start {music_start:.1f}с -> прилип к началу фразы "
                      f"{eff_music_start:.1f}с (чистый вход в трек)")
        elif music_start <= 0.5:
            # берём начало трека -> начинаем с ПЕРВОГО СЛЫШИМОГО ЗВУКА, не с
            # тишины/фейд-ина (пользователь: «музыка с 5 секунды, так не надо»)
            fs = float(info.get("first_sound", 0.0))
            if fs > 0.15:
                eff_music_start = fs
                print(f"  старт трека -> первый звук на {fs:.2f}с (без тихого интро)")
        music_start = eff_music_start

        phrase_cap = snap_to_phrase_end(info, min_total_sec=33.0 + music_start,
                                        max_total_sec=max_total + music_start)
        print(f"  Граница фразы (конец куплета/секции) ~{phrase_cap:.2f}с "
              f"(лимит был {max_total:.0f}с, старт трека {music_start:.0f}с)")
        # phrase_cap абсолютный (от начала трека), а build_shot_grid считает
        # БЮДЖЕТ ДЛИТЕЛЬНОСТИ от первого шота -> вычитаем music_start
        shots = build_shot_grid(info, beats_per_bar, target_shot, min_clip,
                                phrase_cap - music_start, music_start=music_start,
                                force_onset_grid=onset_grid)
        if len(shots) < 2:
            print("  Не удалось построить сетку планов из битов трека — пропуск"); continue

        segs = pick_music_synced_segments(cfg, catalog, spec["candidate_idx"], shots, min_clip,
                                          exclude_specs=exclude_specs, prefer_specs=prefer_specs,
                                          extend_map=extend_map, window_map=window_map,
                                          speed_map=speed_map, open_with=open_with,
                                          landscape=landscape)
        if len(segs) < 2:
            print("  Недостаточно годных клипов под эту сетку — пропуск"); continue

        # --end-with <idx|idx.seg>: ручная правка порядка — закрыть рилс конкретным
        # планом (напр. не церковью, а горой — чтобы петля Reels/TikTok рифмовалась с хуком)
        for want in end_with:
            pos = next((k for k, s in enumerate(segs)
                       if want == str(s["idx"]) or want == s.get("key")), None)
            if pos is not None and pos != len(segs) - 1:
                segs.append(segs.pop(pos))

        if speed_map:
            segs = apply_speedup(catalog, segs, speed_map, None)
        if speedup_keys:
            segs = apply_speedup(catalog, segs, speedup_keys, speedup_factor)
        if zoom_keys:
            segs = apply_zoom(segs, zoom_keys, zoom_amount)

        # ПЕРЕ-СНЭП ГРАНИЦ НА ОНСЕТЫ ПОСЛЕ ПЕРЕСТАНОВОК. --end-with (и любой
        # реордер) перемешивает длительности слотов: грид снэпил КУМУЛЯТИВНЫЕ
        # позиции склеек для ИСХОДНОГО порядка, а после перестановки чуть разные
        # длительности (3.97 vs 4.00с) складываются иначе -> локальные склейки
        # уплывают с онсета на 90-200мс (пользователь: Вьетнам-фильм «2:41 не в
        # бит»). Возвращаем каждую границу на ближайший СИЛЬНЫЙ онсет, меняя
        # длительность слота (src_out) в пределах доступного материала и min_clip.
        # Клипы со скоростью (speed) не трогаем — у них src<->render связаны факт.
        _strong = np.array(sorted(info.get("strong_beats") or info.get("beats") or []))
        if len(_strong) >= 3:
            _dur_by_path = {c["path"]: c.get("duration_sec", 1e9) for c in catalog["clips"]}
            _FPS = 29.97
            _q = lambda x: round(x * _FPS) / _FPS
            _pos = shots[0]["t"]
            for _s in segs:
                _d0 = _s["src_out"] - _s["src_in"]
                if _s.get("speed", 1.0) != 1.0:          # сжатый по времени — не двигаем
                    _pos += _d0; continue
                _want = _pos + _d0
                _clipdur = _dur_by_path.get(_s["path"], 1e9)
                # кандидаты — сильные онсеты в допуске, отсортированы ПО БЛИЗОСТИ к
                # желаемой границе; берём ПЕРВЫЙ, что влезает в материал клипа и не
                # короче min_clip. Важно перебирать, а не брать только ближайший:
                # ближайший часто уезжает ЗА конец материала (кульминация -> клип
                # кончился), а годный онсет с другой стороны — чуть дальше.
                _cand = _strong[(_strong >= _pos + min_clip)
                                & (np.abs(_strong - _want) <= 0.5)]
                _cand = _cand[np.argsort(np.abs(_cand - _want))]
                _moved = False
                for _c in _cand:
                    _nd = _q(float(_c)) - _pos
                    if (_nd >= min_clip - 1e-6
                            and _s["src_in"] + _nd <= _clipdur + 1e-6):
                        _s["src_out"] = _s["src_in"] + _nd
                        _pos = _pos + _nd
                        _moved = True
                        break
                if not _moved:
                    _pos += _d0

        total = sum(s["src_out"] - s["src_in"] for s in segs)
        bar_dur = shots[1]["t"] - shots[0]["t"] if len(shots) > 1 else 0
        print(f"  Темп трека: {info['tempo_bpm']} BPM, план ~{bar_dur:.2f}с "
              f"({beats_per_bar} bts/такт)")
        print(f"  Планов: {len(segs)}, длина: {total:.1f}с")
        for j, s in enumerate(segs):
            tag = "ХУК " if j == 0 else f"{j+1:>4}"
            print(f"  {tag} {s['name']}  src {s['src_in']:.1f}-{s['src_out']:.1f}s")

        audio_path = os.path.join(music_dir, track_file)
        audio_start = shots[0]["t"]

        stub = slugify(track_file)
        out_name = f"{reel_name}__{stub}"
        base = os.path.join(out_dir, out_name)

        fps = 29.97
        tl = build_timeline(segs, name=out_name, fps=fps, audio={
            "path": audio_path, "src_in": audio_start, "src_out": audio_start + total,
            "name": track_file,
        })
        for w in export_timeline(tl, base):
            print(f"  таймлайн: {os.path.relpath(w, ROOT)}")

        prev = base + "_preview.mp4"
        print(f"  рендер превью ({render_res[0]}x{render_res[1]}, с аудио, "
              f"fade-out {end_fade:.1f}с)...")
        if render_preview_with_audio(cfg, segs, audio_path, audio_start, total, prev,
                                     fade=end_fade, res=render_res):
            mb = os.path.getsize(prev) / 1e6
            print(f"  превью: {os.path.relpath(prev, ROOT)} ({mb:.1f} MB)")

    print("=" * 70)
    print("Готово. Превью — НЕ финал: без цветокора, низкое разрешение.")


if __name__ == "__main__":
    main()
