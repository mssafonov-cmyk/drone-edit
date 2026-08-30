# -*- coding: utf-8 -*-
"""
ГЕЙТ ПЕРЕД РЕНДЕРОМ (и аудит уже готового). Прогоняет план кадров (<out>.shots.json
от assemble_from_plan) через doctrine.check_shot: локация/человек/повороты/статика/
мёртвый вход/спайк/джамп-кат/длина/брак. Опц. меряет попадание в бит по РЕНДЕРУ
(scenedetect) против границ плана — как учит rhythm-and-render.md (не по OTIO, не
frame-diff).

Выход: отчёт + код возврата (1 если есть 'hard'-нарушения). Так ассистент физически
не отдаёт кривой монтаж — урок живёт в коде, а не в памяти.

Использование:
  python validate_shots.py <out.shots.json> [--batch vietnam] [--location halong]
                           [--render <out.mp4>] [--film]
"""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doctrine as DOC
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def arg(flag, d=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else d


def measure_beat(render, plan_start, shots):
    """Реальные склейки в рендере (scenedetect) vs границы планов. Возвращает
    (макс_отклонение_мс, [отклонения])."""
    try:
        from scenedetect import detect, ContentDetector
    except Exception as e:
        return None, f"scenedetect недоступен: {e}"
    scenes = detect(render, ContentDetector(27, min_scene_len=15))
    cuts = [s[1].seconds for s in scenes[:-1]] if scenes else []
    exp, acc = [], 0.0
    for s in shots[:-1]:
        acc += s["dur_sec"]; exp.append(acc)
    devs, unsure = [], 0
    for e in exp:
        near = [abs(e - c) for c in cuts if abs(e - c) < 0.35]
        if near:
            devs.append(min(near) * 1000.0)
        else:
            # scenedetect не увидел эту склейку (похожие кадры: туман/вода) — меряем
            # ЛОКАЛЬНО frame-diff: пик разницы кадров в ±0.4с должен лечь в ±40мс от e.
            d = _local_cut_dev(render, e)
            if d is None: unsure += 1
            else: devs.append(d)
    note = f" ({unsure} склеек неразличимы — похожие кадры)" if unsure else ""
    return (max(devs) if devs else 0.0), (devs, note)


def _local_cut_dev(render, e, win=0.4):
    """Отклонение реальной склейки от ожидаемой t=e по ЛОКАЛЬНОМУ пику frame-diff."""
    import subprocess, os as _os, numpy as np
    try:
        import cv2
    except Exception:
        return None
    ff = _ffmpeg(); tmp = _os.path.join(ROOT, "work", "_beatchk"); _os.makedirs(tmp, exist_ok=True)
    for f in _os.listdir(tmp):
        _os.remove(_os.path.join(tmp, f))
    t0 = max(0, e - win)
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.3f}",
                    "-t", f"{2*win:.3f}", "-i", render, "-vf", "scale=120:213",
                    _os.path.join(tmp, "f%03d.png")], capture_output=True)
    fs = sorted(_os.listdir(tmp)); prev = None; diffs = []
    for f in fs:
        g = cv2.cvtColor(cv2.imread(_os.path.join(tmp, f)), cv2.COLOR_BGR2GRAY).astype(float)
        if prev is not None:
            diffs.append(float(np.abs(g - prev).mean()))
        prev = g
    if len(diffs) < 5:
        return None
    a = np.array(diffs)
    k = int(np.argmax(a))
    if a[k] < np.median(a) * 2.5 + 0.5:      # пика нет — склейка неразличима глазом/метрикой
        return None
    fps = 30000 / 1001
    t_cut = t0 + (k + 1) / fps
    return abs(t_cut - e) * 1000.0


def _ffmpeg():
    import yaml
    return yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))["tools"]["ffmpeg"]


def scan_luma(render):
    """Средняя яркость КАЖДОГО кадра (signalstats YAVG, один проход). -> [(t, Y)]."""
    import subprocess, re
    ff = _ffmpeg()
    r = subprocess.run([ff, "-hide_banner", "-i", render, "-vf",
                        "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                        "-f", "null", "-"], capture_output=True, text=True)
    out = []
    t = None
    for line in (r.stderr or "").splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m: t = float(m.group(1))
        m = re.search(r"YAVG[=:]([0-9.]+)", line)
        if m and t is not None: out.append((t, float(m.group(1))))
    return out


def find_flashes(luma):
    """Одиночные кадры-выбросы яркости (вспышка на стыке/глюк): кадр отличается от
    ОБОИХ соседей > 16 и в ту же сторону. Возвращает [(t, Y, соседи)]."""
    fl = []
    for i in range(1, len(luma) - 1):
        (t, y), (_, yp), (_, yn) = luma[i], luma[i - 1], luma[i + 1]
        if (y - yp > 16 and y - yn > 16) or (yp - y > 16 and yn - y > 16):
            fl.append((t, y, (round(yp), round(yn))))
    return fl


def finale_motion(render, dur):
    """Движение в последних 2.5с: растёт (улетает) или оседает. -> (mean, trend)."""
    import subprocess, os as _os, numpy as np
    try:
        import cv2
    except Exception:
        return None
    ff = _ffmpeg(); tmp = _os.path.join(ROOT, "work", "_finchk"); _os.makedirs(tmp, exist_ok=True)
    for f in _os.listdir(tmp):
        _os.remove(_os.path.join(tmp, f))
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0,dur-2.5):.2f}",
                    "-t", "2.5", "-i", render, "-vf", "scale=200:356,fps=12", _os.path.join(tmp, "f%03d.png")],
                   capture_output=True)
    fs = sorted(_os.listdir(tmp)); prev = None; mv = []
    for f in fs:
        g = cv2.cvtColor(cv2.imread(_os.path.join(tmp, f)), cv2.COLOR_BGR2GRAY).astype(float)
        if prev is not None: mv.append(float(np.abs(g - prev).mean()))
        prev = g
    if len(mv) < 4: return None
    half = len(mv) // 2
    trend = np.mean(mv[half:]) - np.mean(mv[:half])   # >0 = ускоряется к концу (улетает)
    return float(np.mean(mv)), float(trend)


def tail_turns(render, shots, tail=0.8):
    """ХВОСТОВОЙ ДОВОРОТ (покадрово, надёжно — 1Гц ang не годился): на конце каждого
    плана меряем поворот направления оптpotока. Возвращает [(слот, градусы_поворота)]."""
    import subprocess, os as _os, numpy as np
    try:
        import cv2
    except Exception:
        return []
    ff = _ffmpeg(); tmp = _os.path.join(ROOT, "work", "_tailchk"); _os.makedirs(tmp, exist_ok=True)
    ends, acc = [], 0.0
    for s in shots:
        acc += s["dur_sec"]; ends.append(acc)
    res = []
    for i, ct in enumerate(ends):
        for f in _os.listdir(tmp):
            _os.remove(_os.path.join(tmp, f))
        # НЕ доезжаем 0.12с до склейки: захват кадра следующего плана читается как
        # ложный «разворот/всплеск» (сама склейка = огромный diff).
        # МИКРО-ПЛАНЫ (урок 2026-08-09): окно хвоста <= 60% длины плана, иначе на кусках
        # ~1с окно упирается в ПРЕДЫДУЩУЮ склейку (сик цепляет чужой кадр -> ложный всплеск).
        tail_i = min(tail, shots[i]["dur_sec"] * 0.6)
        if tail_i < 0.35:
            res.append((i + 1, 0.0)); continue
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0,ct-tail_i-0.12):.2f}",
                        "-t", f"{max(0.3,tail_i-0.12):.2f}", "-i", render, "-vf", "scale=160:284,fps=15",
                        _os.path.join(tmp, "f%03d.png")], capture_output=True)
        fs = sorted(_os.listdir(tmp))
        if len(fs) < 4:
            res.append((i + 1, 0.0)); continue
        prev = None; vs = []
        for f in fs:
            g = cv2.cvtColor(cv2.imread(_os.path.join(tmp, f)), cv2.COLOR_BGR2GRAY)
            if prev is not None:
                fl = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                vs.append((float(fl[..., 0].mean()), float(fl[..., 1].mean())))
            prev = g
        # РАЗВОРОТ: смена знака доминантной оси + всплеск скорости (урок 2026-08-02:
        # доворот на 11с — камера ехала вправо 0.02, развернулась влево до 1.0; старый
        # детектор отбрасывал слабое движение и не видел смену знака).
        rot = 0.0
        if len(vs) > 3:
            dxs = np.array([v[0] for v in vs]); dys = np.array([v[1] for v in vs])
            dom = dxs if np.abs(dxs).mean() >= np.abs(dys).mean() else dys
            sp = np.hypot(dxs, dys)
            # знаки только там, где |v| заметна ОТНОСИТЕЛЬНО пика хвоста
            thr = max(0.05, float(sp.max()) * 0.15)
            sgn = [np.sign(d) for d, s in zip(dom, sp) if s > thr and abs(d) > thr * 0.7]
            reversed_dir = any(sgn[i] != sgn[i + 1] for i in range(len(sgn) - 1))
            burst = float(sp.max()) / max(0.02, float(np.median(sp[:max(2, len(sp)//3)])))
            if reversed_dir and float(sp.max()) > 0.3:
                rot = 180.0                       # разворот направления = грубый доворот
            elif burst > 6 and float(sp.max()) > 0.5:
                rot = 90.0                        # резкий всплеск скорости в хвосте
        res.append((i + 1, rot))
    return res


def main():
    sj = json.load(open(sys.argv[1], encoding="utf-8"))
    batch = arg("--batch", "vietnam"); location = arg("--location")
    film = "--film" in sys.argv
    cat_p = os.environ.get("CLIP_CATALOG") or os.path.join(ROOT, "work", "clip_catalog.json")
    cat = json.load(open(cat_p, encoding="utf-8"))
    by_idx = {c["idx"]: c for c in cat["clips"]}
    person_idx, loc_idx, allow_spin = (), None, False
    cur_p = os.path.join(ROOT, "work", f"curation_{batch}.json")
    if os.path.exists(cur_p):
        cur = json.load(open(cur_p, encoding="utf-8"))
        person_idx = tuple(cur.get("person_in_frame", {}).get("idx", []))
        allow_spin = bool(cur.get("orbits_ok"))
        if location and location in cur.get("locations", {}):
            loc_idx = set(cur["locations"][location]["idx"])

    shots = sj["shots"]
    print(f"== ВАЛИДАЦИЯ {sj.get('out','')} | партия {batch} | локация {location or '—'} | {len(shots)} планов ==")
    n_hard = n_soft = 0; prev = None
    for s in shots:
        clip = by_idx.get(s["idx"])
        if not clip:
            print(f"  слот {s['slot']}: idx{s['idx']} НЕТ в каталоге"); n_hard += 1; continue
        viol = DOC.check_shot(clip, s["win_start"], s["dur_sec"],
                              is_open=(s["slot"] == 1), forced=s.get("forced", False),
                              prev_idx=prev, location_idx=loc_idx, person_idx=person_idx,
                              film=film, allow_spin=allow_spin)
        prev = s["idx"]
        hard = [x for x in viol if x["sev"] == "hard"]; soft = [x for x in viol if x["sev"] == "soft"]
        n_hard += len(hard); n_soft += len(soft)
        tag = "OK " if not viol else ("❌HARD" if hard else "⚠ soft")
        print(f"  слот {s['slot']:2d} idx{s['idx']:3d} {s['win_start']:5.1f}+{s['dur_sec']:.1f}с  {tag}")
        for x in hard + soft:
            print(f"        {'❌' if x['sev']=='hard' else '⚠'} {x['rule']}: {x['detail']}")

    render = arg("--render")
    if render and os.path.exists(render):
        mx, devs = measure_beat(render, sj.get("start_time", 0), shots)
        if isinstance(devs, str):
            print(f"  бит: {devs}")
        else:
            note = devs[1] if isinstance(devs, tuple) else ""
            ok = mx <= DOC.D["beat_dev_ms"]
            print(f"  БИТ: макс отклонение {mx:.0f}мс (порог {DOC.D['beat_dev_ms']:.0f}) {'OK' if ok else '❌ мимо'}{note}")
            if not ok: n_hard += 1
        # ВСПЫШКИ (одиночные кадры-выбросы яркости по ВСЕМУ ролику) — самопроверка на глюки
        luma = scan_luma(render)
        flashes = find_flashes(luma)
        if flashes:
            print(f"  ❌ ВСПЫШКИ (одиночные кадры): {len(flashes)}")
            for t, y, nb in flashes[:6]:
                print(f"        t={t:.2f}с Y{y:.0f} против соседей {nb}")
            n_hard += len(flashes)
        else:
            print(f"  вспышки: нет (проверено {len(luma)} кадров)")
        # ХВОСТОВЫЕ ДОВОРОТЫ/РАЗВОРОТЫ (покадрово): разворот направления или всплеск
        # скорости в хвосте = ГРУБО (hard, пользователь: «серьёзный доворот»).
        # САЛЮТ/субъект-движется-сам (curation subject_motion_idx): развороты потока
        # создаёт сам объект (вспышки) — хвостовой детект для них не hard (доктрина).
        _subj = set()
        if os.path.exists(cur_p):
            _subj = set(json.load(open(cur_p, encoding="utf-8")).get("subject_motion_idx", []))
        tt = [(sl, r) for sl, r in tail_turns(render, shots, tail=1.0)
              if r > 22 and shots[sl - 1]["idx"] not in _subj]
        for sl, r in tt:
            kind = "РАЗВОРОТ" if r >= 180 else ("всплеск скорости" if r >= 90 else "доворот")
            hard_t = r >= 90
            print(f"  {'❌' if hard_t else '⚠'} хвост слота {sl}: {kind}")
            if hard_t: n_hard += 1
            else: n_soft += 1
        # ПОХОЖИЕ СОСЕДИ (#6, 2026-08-08): визуально похожие смежные планы читаются как
        # джамп-кат, даже если клипы разные. Гистограммная корреляция mid-кадров.
        try:
            import cv2, numpy as _np, subprocess as _sp
            tmpd = os.path.join(ROOT, "work", "_simchk"); os.makedirs(tmpd, exist_ok=True)
            mids, acc2 = [], 0.0
            for s in shots:
                mids.append(acc2 + s["dur_sec"] / 2); acc2 += s["dur_sec"]
            hists = []
            for i, t in enumerate(mids):
                fp = os.path.join(tmpd, "m.png")
                _sp.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
                         "-i", render, "-frames:v", "1", "-vf", "scale=160:284", fp], capture_output=True)
                im = cv2.imread(fp)
                if im is None: hists.append(None); continue
                hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
                h = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
                cv2.normalize(h, h); hists.append(h)
            sim_pairs = []
            for i in range(len(hists) - 1):
                if hists[i] is not None and hists[i + 1] is not None:
                    c = float(cv2.compareHist(hists[i], hists[i + 1], cv2.HISTCMP_CORREL))
                    if c > 0.93:
                        sim_pairs.append((i + 1, i + 2, c))
            if sim_pairs:
                print("  ⚠ похожие соседние планы (риск джамп-ката): " +
                      ", ".join(f"{a}-{b} ({c:.2f})" for a, b, c in sim_pairs))
                n_soft += len(sim_pairs)
        except Exception:
            pass
        # СООТВЕТСТВИЕ КОНТЕНТА (2026-08-17, шары v2: в рендере остался СТАРЫЙ кусок при
        # свежем shots.json — упавший шаг цепочки молча оставил прежний файл, метаданные
        # разошлись с видео). Кадр из середины каждого слота рендера сверяется с кадром
        # исходника той же точки окна: низкая корреляция = подмена/старьё -> HARD.
        # Нормировка по яркости — грейд не мешает. Кроп-геометрия 16:9->9:16 сажает
        # корреляцию умеренно, порог 0.25 это учитывает.
        try:
            import numpy as _np2, subprocess as _sp2
            tmpc = os.path.join(ROOT, "work", "_contentchk"); os.makedirs(tmpc, exist_ok=True)
            def _gray64(path, t, out):
                _sp2.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                          "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                          "-vf", "scale=64:64", "-f", "rawvideo", "-pix_fmt", "gray", out],
                         capture_output=True)
                if not os.path.exists(out) or os.path.getsize(out) < 4096:
                    return None
                a = _np2.fromfile(out, _np2.uint8)[:4096].astype(_np2.float32)
                return (a - a.mean()) / (a.std() + 1e-6)
            _accc = 0.0; _badc = []
            for s in shots:
                _midr = _accc + s["dur_sec"] / 2; _accc += s["dur_sec"]
                _clip = by_idx.get(s["idx"])
                if not _clip: continue
                A = _gray64(render, _midr, os.path.join(tmpc, "a.raw"))
                B = _gray64(_clip["path"], s["win_start"] + s["dur_sec"] / 2,
                            os.path.join(tmpc, "b.raw"))
                if A is None:
                    # кадр за пределами рендера = рендер КОРОЧЕ плана (обрезанный фильм
                    # 2026-08-18 маскировался continue) -> это hard, не пропуск.
                    _badc.append((s["slot"], s["idx"], -9.99)); continue
                if B is None: continue
                c = float((A * B).mean())
                if c < 0.25: _badc.append((s["slot"], s["idx"], c))
            if _badc:
                for sl, ix, c in _badc:
                    print(f"  ❌ КОНТЕНТ слота {sl}: не совпадает с исходником idx{ix} (корр {c:.2f}) — подмена/старый кусок")
                n_hard += len(_badc)
            else:
                print(f"  контент: все {len(shots)} слотов совпадают с исходниками")
        except Exception as _e:
            print("  ⚠ контент-проверка не отработала:", _e); n_soft += 1
        # повтор одного клипа (idx36 8с≈37с читались как один ролик)
        from collections import Counter as _C
        rep = {i: c for i, c in _C(s["idx"] for s in shots).items() if c > 1}
        if rep:
            print(f"  ⚠ клип использован несколько раз (риск повтора): " + ", ".join(f"idx{i}x{c}" for i, c in rep.items()))
        # ФИНАЛ — оседает или улетает
        fm = finale_motion(render, luma[-1][0] if luma else 0)
        if fm:
            mean, trend = fm
            fly = trend > 1.0 and mean > 3.0
            print(f"  ФИНАЛ: движение {mean:.1f}, тренд {trend:+.1f} {'❌ улетает (ускоряется к концу)' if fly else 'оседает/ок'}")
            if fly: n_soft += 1

    print(f"\nИТОГО: {n_hard} hard, {n_soft} soft. {'❌ НЕ ОТДАВАТЬ' if n_hard else '✅ можно отдавать'}")
    sys.exit(1 if n_hard else 0)


if __name__ == "__main__":
    main()
