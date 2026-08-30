# -*- coding: utf-8 -*-
"""
Этап 5 v3 — КОНТЕНТ-АДАПТИВНЫЙ натуральный грейд («влажная кинематографичная природа»).

Порядок (по брифу пользователя/ChatGPT, строго):
  0. Профиль исходников — Rec.709 (ffprobe: DJI, bt709, 8 бит, БЕЗ лога/LUT). CST не нужен.
  1. ТЕХНИЧЕСКАЯ КОРРЕКЦИЯ каждого плана В ПОРЯДКЕ:
       экспозиция -> ББ+Tint -> точка чёрн/бел (АДАПТИВНО по IRE) -> контраст/миды -> насыщ.
  2. ВЫРАВНИВАНИЕ по сцене: экспозиция тянется к медиане СВОЕЙ группы (не общей).
  3. СЛАБЫЙ художественный лук, СВОЙ на тип сцены (БЕЗ teal-orange):
       green   -> «wet nature» (прохладные тени, нейтр. облака, зелень к изумруду);
       warm    -> тёплый-нейтральный (скала/вулкан, лёгкое тепло в светах);
       neutral -> дымка/дали: почти не трогаем, чуть холоднее и мягче (ChatGPT: дальние холоднее).

Принцип (зашит): не максимизировать насыщенность/контраст/Dehaze. База решает, лук слабый.
Контроль по scopes: IRE тени/лес/света; АДАПТИВНО тянем света под ~93 и тени над ~6.

Использование:
  python stage5_grade_v3.py <preview.mp4> <timeline.otio> <out.mp4> [strength] [green=a,b warm=c]
Пишет также <out>__COMPARE.jpg (до / техническая / финал) на green- и warm-плане.
"""
import os, sys, json, subprocess
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage5_grade_match import (load_cfg, shot_bounds_from_otio, refine_bounds, measure)


def classify(st):
    """3 типа: green (лес), warm (скала/золото), neutral (дымка/дали/серое)."""
    r, g, b = st["r"], st["g"], st["b"]
    if g - (r + b) / 2.0 > 4.0:
        return "green"
    if (r - b) > 12.0 and r >= g - 2:
        return "warm"
    return "neutral"


def measure_ire(cfg, video, t0, t1, n=3):
    ffmpeg = cfg["tools"]["ffmpeg"]; w, h = 160, 90
    dur = max(0.2, t1 - t0); step = dur / (n + 1); lumas = []
    for k in range(1, n + 1):
        t = t0 + step * k
        out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
               "-i", video, "-frames:v", "1", "-vf", f"scale={w}:{h}",
               "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True).stdout
        if len(out) >= w * h:
            lumas.append(np.frombuffer(out[:w * h], np.uint8).astype(np.float32))
    if not lumas:
        return None
    y = np.concatenate(lumas)
    ire = lambda v: max(0.0, min(100.0, (v - 16.0) / 219.0 * 100.0))
    return {"lo": ire(np.percentile(y, 5)), "mid": ire(np.percentile(y, 50)),
            "hi": ire(np.percentile(y, 95))}


def measure_robust_luma(cfg, video, t0, t1, n=3):
    """Устойчивая экспозиция: средний тон БЕЗ выбитого неба/тумана (>85 перц.) и
    без глубоких теней (<5 перц.). Иначе туман (яркий по площади) выглядит
    «переэкспонированным» и алгоритм зря его гасит (провал репозитория-каркаса)."""
    ffmpeg = cfg["tools"]["ffmpeg"]; w, h = 160, 90
    dur = max(0.2, t1 - t0); step = dur / (n + 1); ys = []
    for k in range(1, n + 1):
        t = t0 + step * k
        out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
               "-i", video, "-frames:v", "1", "-vf", f"scale={w}:{h}",
               "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True).stdout
        if len(out) >= w * h:
            ys.append(np.frombuffer(out[:w * h], np.uint8).astype(np.float32))
    if not ys:
        return None
    y = np.concatenate(ys)
    lo, hi = np.percentile(y, 5), np.percentile(y, 85)
    band = y[(y >= lo) & (y <= hi)]
    return float(band.mean()) if len(band) else float(y.mean())


def measure_fog(cfg, video, t0, t1, n=2):
    """Доля ЯРКИХ МАЛОНАСЫЩЕННЫХ пикселей (туман/облака) — для авто-детекта foggy."""
    ffmpeg = cfg["tools"]["ffmpeg"]; w, h = 120, 68
    dur = max(0.2, t1 - t0); step = dur / (n + 1); fr = []
    for k in range(1, n + 1):
        t = t0 + step * k
        out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
               "-i", video, "-frames:v", "1", "-vf", f"scale={w}:{h}",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True).stdout
        if len(out) >= w * h * 3:
            fr.append(np.frombuffer(out[:w*h*3], np.uint8).reshape(h, w, 3).astype(np.float32))
    if not fr:
        return 0.0
    img = np.concatenate(fr, axis=0)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    luma = 0.299*r + 0.587*g + 0.114*b
    sat = img.max(axis=2) - img.min(axis=2)
    return float(np.mean((luma > 150) & (sat < 38)))


def measure_overcast(cfg, video, t0, t1, n=2):
    """Доля ярких малонасыщенных пикселей в ВЕРХНЕЙ трети (пасмурное серое небо).
    Отличается от foggy (туман по всему кадру) — тут небо сверху, передний план чистый."""
    ffmpeg = cfg["tools"]["ffmpeg"]; w, h = 120, 68
    dur = max(0.2, t1 - t0); step = dur / (n + 1); fracs = []
    for k in range(1, n + 1):
        t = t0 + step * k
        out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
               "-i", video, "-frames:v", "1", "-vf", f"scale={w}:{h}",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True).stdout
        if len(out) >= w * h * 3:
            img = np.frombuffer(out[:w*h*3], np.uint8).reshape(h, w, 3).astype(np.float32)
            top = img[:int(h * 0.38)]
            r, g, b = top[..., 0], top[..., 1], top[..., 2]
            luma = 0.299*r + 0.587*g + 0.114*b
            sat = top.max(axis=2) - top.min(axis=2)
            # РАЗДЕЛИТЕЛЬ (замерено): overcast = СРЕДНЯЯ насыщенность верха НИЗКАЯ (серо,
            # ~12), а закат/дымка/голубое небо имеют цвет (sat ~21-26). Плюс небо яркое и
            # +/- нейтральное. Так свинцовые тучи ловятся, а закат reel01 — нет.
            ms, ml, mrb = float(sat.mean()), float(luma.mean()), float((r - b).mean())
            fracs.append(1.0 if (ms < 18 and ml > 150 and abs(mrb) < 14) else 0.0)
    return float(np.mean(fracs)) if fracs else 0.0


def gain(cur, target, s, lo, hi):
    if cur <= 1e-6:
        return 1.0
    return float(min(hi, max(lo, (target / cur) ** s)))


def levels_filter(gr, gg, gb):
    parts = []
    for ch, g in (("r", gr), ("g", gg), ("b", gb)):
        parts.append(f"{ch}imax={min(1.0, 1.0/g):.4f}" if g >= 1.0 else f"{ch}omax={g:.4f}")
    return "colorlevels=" + ":".join(parts)


def technical_chain(st, shot_luma, ref_luma, klass, ire, strength=0.55):
    """ШАГ 1 по порядку. 8 бит -> мягко. Точка чёрн/бел АДАПТИВНА по IRE.
    Экспозиция выравнивается по УСТОЙЧИВОМУ среднему тону (shot_luma), не по mean
    всего кадра — туман/лес не равняем по яркости (это контент, не экспозиция)."""
    g_luma = gain(shot_luma, ref_luma, strength, 0.88, 1.15)          # 1 экспозиция (робастно)
    # ВЫСОКИЙ КЛЮЧ (2026-08-17, снежные поля YAVG 162->101 = «совсем без качества»):
    # яркая воздушная сцена (высокая IRE-медиана) — её яркость КОНТЕНТ, не пересвет;
    # к среднему ref_luma не давим. Пол гейна плавно поднимается к 1.0 (mid>=62).
    if ire and ire.get("mid", 0) >= 50:
        hk = max(0.0, min(1.0, (float(ire["mid"]) - 50.0) / 12.0))
        g_luma = max(g_luma, 0.88 + 0.12 * hk)
    if klass == "night":                                              # ночь/синий час
        # холоднее в целом (синяя вода/небо), но БОГАТЫЕ чёрные и ЯРКИЕ огни —
        # против «блёклости на телефоне». Контраст даёт лук, тут только база.
        gr, gg, gb = 1.0, 0.985, 1.025
        # лёгкий подъём экспозиции для ночи (пользователь: «чутка темновато»),
        # но в потолке — чтобы огни не выбить
        g_luma = min(1.15, g_luma * 1.06)
        gr *= g_luma; gg *= g_luma; gb *= g_luma
        f = levels_filter(gr, gg, gb)
        # приподняты чёрная точка и СРЕДНИЕ тона (ярче на телефоне), белая почти в
        # потолок (огни); мягкая S сохранена -> пунч не теряем
        f += ",curves=all='0/0.014 0.25/0.26 0.5/0.55 0.75/0.80 1/0.99'"
        return f  # без sat-down: ночью нужен цвет, не выхолащивать
    if klass == "overcast":                                          # пасмурное серое небо
        # ОДНОЙ КРИВОЙ: света ВНИЗ (небо в свинец) + тени/средние ВВЕРХ (передний план).
        # Композиция «небо=света, передний план=средние» -> без масок/градиента.
        gr, gg, gb = 1.0, 0.99, 1.006
        gr *= g_luma; gg *= g_luma; gb *= g_luma
        f = levels_filter(gr, gg, gb)
        f += ",curves=all='0/0.02 0.25/0.28 0.5/0.55 0.75/0.66 1/0.80'"
        return f
    if klass == "foggy":                                             # туман/дымка
        # ТЯЖЁЛОЕ небо: сильнее прибираем света (облака драматичнее), чуть холоднее
        gr, gg, gb = 1.0, 0.985, 1.015
        gr *= g_luma; gg *= g_luma; gb *= g_luma
        f = levels_filter(gr, gg, gb)
        # ТЯЖЁЛОЕ небо (сильнее прибираем света -> драматичные облака)
        f += ",curves=all='0/0.012 0.25/0.23 0.5/0.47 0.75/0.62 1/0.80'"
        return f
    if klass == "green":                                              # 2 ББ+Tint
        # МЯГКО: сильный глобальный magenta красит нейтральные облака в розовое
        # (проверено на превью). Каст зелени убираем в основном selectivecolor'ом
        # (только зелёные пиксели), а глобально — чуть-чуть.
        gr, gg, gb = 1.006, 0.984, 1.008     # еле-еле нейтрализуем зелёно-жёлтый туман
    elif klass == "neutral":
        gr, gg, gb = 1.0, 0.99, 1.01         # чуть холоднее (дали)
    elif klass == "golden":
        gr, gg, gb = 1.012, 1.0, 0.982       # рассветный контражур: тёплая база
    else:
        gr, gg, gb = 1.0, 0.995, 1.0         # warm — почти нейтрально
    gr *= g_luma; gg *= g_luma; gb *= g_luma
    f = levels_filter(gr, gg, gb)
    # 3 точка чёрн/бел АДАПТИВНО: света под ~93 если клипят, чёрное над ~5
    hi = ire["hi"] if ire else 90
    lo = ire["lo"] if ire else 15
    top = 0.945 if hi > 95 else (0.96 if hi > 90 else 0.975)
    bot = 0.028 if lo < 5 else (0.02 if lo < 9 else 0.014)
    # 4 контраст: мягкая S на этих точках
    f += f",curves=all='0/{bot:.3f} 0.25/0.238 0.5/0.5 0.75/0.762 1/{top:.3f}'"
    f += ",eq=saturation=0.96"                                        # 5 насыщ мягко вниз
    return f


def look_chain_v4(klass, mid_ire=None):
    """ОБЪЁМНЫЙ лук v4 (утверждён 2026-08-08 по A/B на Сейшелах, из YouTube-референсов):
    filmic-кривая (приподнятый чёрный, мягкие света) + локальный контраст 2 радиусов
    (аналог dodge&burn — лепит рельеф) + мягкая виньетка (градиент глубины) +
    приглушённая база + селективный вибранс + тил-тени/тёплые света.
    ВНИМАНИЕ: содержит split/blend — только для filter_complex (per-file рендер), не -vf."""
    # ВЫСОКИЙ КЛЮЧ (2026-08-17): на ярких воздушных сценах (снег, mid>=55) виньетка =
    # грязь по краям, а подъём чёрного 0.035 = хейз. Виньетку убираем, чёрный ниже.
    hk = mid_ire is not None and float(mid_ire) >= 55
    blk = 0.02 if hk else 0.035
    vin = "" if hk else "vignette=angle=PI/5.2,"
    base = (f"curves=master='0/{blk} 0.22/0.20 0.5/0.51 0.78/0.80 1/0.965',"
            "unsharp=luma_msize_x=13:luma_msize_y=13:luma_amount=0.5,"
            "split[v4a][v4b];[v4b]gblur=sigma=22[v4bb];"
            f"[v4a][v4bb]blend=all_mode=softlight:all_opacity=0.30,{vin}")
    if klass == "golden":
        # РАССВЕТНЫЙ/ЗАКАТНЫЙ КОНТРАЖУР (2026-08-17, шары «покрасить в тёплые цвета»):
        # fresh-ветка красила солнечную дымку зеленью (gh=0.03) — тут наоборот: греем
        # СВЕТА и СРЕДНИЕ (солнце золотом), лёгкий тил только в тенях, зелень в светах
        # запрещена. Ставится вручную: --warm-slots "13,14" (авто-детект ненадёжен).
        color = ("eq=saturation=0.96,vibrance=intensity=0.30,"
                 "colorbalance=bs=0.04:rm=0.065:gm=0.01:bm=-0.045:rh=0.12:bh=-0.06")
        return base + color
    if klass == "night":
        # ночь: неон не глушим, виньетка уже есть в кадре — цвет мягче
        color = "eq=saturation=0.96,vibrance=intensity=0.18,colorbalance=bs=0.05:bh=-0.02"
    elif klass == "warm":
        color = "eq=saturation=0.90,vibrance=intensity=0.25,colorbalance=bs=0.05:rh=0.06:bh=-0.04"
    elif klass in ("foggy", "overcast"):
        color = "eq=saturation=0.90,vibrance=intensity=0.22,colorbalance=bs=0.06:gs=0.02:rh=0.03:bh=-0.03"
    else:   # green / neutral — FRESH (2026-08-08: «желтит» на террасах/Garrya):
        # света НЕ греем (rh=0 — тёплый пуш красил зелень в горчицу), лёгкая зелень в светах,
        # насыщенность выше (0.93), вибранс мягче — зелень живая, золото урожая не тронуто
        color = "eq=saturation=0.93,vibrance=intensity=0.22,colorbalance=bs=0.06:gs=0.02:gh=0.03:bh=-0.01"
    return base + color


def look_chain_mobile(klass, mid_ire=None, hi_ire=None, lift=0.0):
    """MOBILE-ФИНАЛ = ПАНЧ-АДАПТИВ (2026-08-10). Базовый ПАНЧ утверждён («на телефоне
    выигрывает»), но статичная S-кривая топила СУМЕРКИ в черноту («очень тёмные видосы»).
    Фикс: характер панча сохраняется, ГЛУБИНА теневого провала смягчается по темноте
    плана (драйвер — IRE-медиана тёмной массы): яркий план = полный панч (тени 0.17,
    чёрный 0.005); тёмный план = мягче (тени до 0.245, чёрный до 0.025, середина +0.05).
    Света ВСЕГДА панчевые (0.85/0.99) — золото горит. split-метки -> filter_complex."""
    b = 0.0
    if mid_ire is not None:
        b = max(0.0, min(1.35, (46.0 - float(mid_ire)) / 32.0))  # ночь (mid<10) добирает до 1.35
        # ЗАЩИТА СВЕТОВ (2026-08-17, лёд Каппадокии «перебор с яркостью»): тёмная
        # медиана при ЯРКОЙ массе в кадре (снег/контражур, p95 высоко) — это не ночь,
        # а высокий контраст сцены. Лифт теней тут раздувает яркость и выбеливает
        # снег. Гасим b по запасу в светах: p95>=88 IRE -> лифта нет, <=66 -> полный.
        if hi_ire is not None:
            b *= max(0.0, min(1.0, (88.0 - float(hi_ire)) / 22.0))
    blk = 0.005 + 0.020 * b
    y22 = 0.17 + 0.075 * b
    y50 = 0.51 + 0.050 * b
    y78, y100, sharp = 0.85, 0.99, 0.55
    if klass == "night":
        # НОЧНОЙ БАЛАНС (2026-08-17, ГК «слишком темный, яркие засвечены»): панч-света
        # 0.85/0.99 толкали и так клипованный неон в белое, а тени оставались чёрными.
        # Ночь: плечо светов вниз (неон с фактурой), тени/середина ГАРАНТИРОВАННО подняты
        # (пол b=0.55 даже если медиана обманула), резкость сильнее — неон любит чёткость.
        b = max(b, 0.55)
        blk = 0.012 + 0.020 * b
        y22 = 0.20 + 0.075 * b
        y50 = 0.53 + 0.050 * b
        y78, y100, sharp = 0.82, 0.955, 0.75
    # --lift L (2026-08-21, «вьетнам тёмный на телефоне»): равномерный подъём середины
    # и теней к эталонной яркости (reel04 опубликован = YAVG 106), света не трогаем.
    if lift:
        # капы подняты (2026-08-29, Халонг/Ниньбинь упирались в 0.62 и не добирали):
        # телефонная читаемость важнее глубины провала; света всё равно панч.
        blk = min(0.06, blk + 0.25 * lift)
        y22 = min(0.335, y22 + 0.8 * lift)
        y50 = min(0.66, y50 + lift)
    return (f"curves=master='0/{blk:.3f} 0.22/{y22:.3f} 0.5/{y50:.3f} 0.78/{y78:.2f} 1/{y100:.2f}',"
            f"unsharp=luma_msize_x=13:luma_msize_y=13:luma_amount={sharp:.2f},"
            "split[pa][pb];[pb]gblur=sigma=22[pc];"
            "[pa][pc]blend=all_mode=softlight:all_opacity=0.35,"
            "vibrance=intensity=0.34,eq=saturation=1.03:contrast=1.05,"
            "colorbalance=bs=0.08:gs=0.02:rh=0.05:bh=-0.04")


def look_chain(klass):
    """ШАГ 3: СЛАБЫЙ лук по типу сцены. БЕЗ teal-orange (кроме ОСОЗНАННОГО night —
    там сине-жёлтый акцент уместен: ночь + тёплые практические огни)."""
    if klass == "night":
        # синие тени/вода, ТЁПЛЫЕ огни в светах, контраст+ (пунчево на телефоне),
        # насыщенность через VIBRANCE (не global) -> без кислоты.
        return ("colorbalance=rs=-0.04:gs=-0.012:bs=0.07:rm=-0.008:gm=0.0:bm=0.018:"
                "rh=0.045:gh=0.012:bh=-0.04,"
                "selectivecolor=blues=0.05 0 -0.06 -0.02:yellows=0 0 0.06 -0.03,"
                "vibrance=intensity=0.20,eq=contrast=1.10")
    if klass == "foggy":
        # тяжёлое небо + холодная зелень + УБРАТЬ синеву дальних гор (blues/cyans −sat).
        # Локальный субъект-dodge — НЕ здесь (Luminar/Resolve), см. [[user-signature-look]].
        return ("colorbalance=rs=-0.02:gs=0.0:bs=0.03:rm=-0.01:gm=-0.02:bm=0.02:"
                "gh=-0.005:bh=0.008,"
                "selectivecolor=correction_method=relative:blues=0 0 0.34 0.14:"
                "cyans=0 0 0.26 0.06:greens=0.10 0 0.08 -0.05,"
                "vibrance=intensity=0.20,eq=contrast=1.06")
    if klass in ("green", "overcast"):   # overcast: та же зелень/изумруд, небо давит техчасть
        # тени чуть прохладнее, СВЕТА НЕЙТРАЛЬНЫ (облака не тонируем): highlights ~0.
        # Изумруд и де-жёлт — ТОЛЬКО по зелёным/жёлтым пикселям (selectivecolor),
        # чтобы небо/туман остались нейтральными. Vibrance умеренный.
        # подпись пользователя (уровень STRONG, утв.): контраст+, vibrance+, ТИЛ-тени,
        # + ВЫБОРОЧНОЕ усиление тёплого неба (reds/magentas) — зелень не трогает.
        # STRONG (утв.): тёплое небо усилено до standalone-уровня (reds/magentas),
        # зелень не трогаем (yellows −sat), тил-тени глубже. vibrance умеренный (джунгли не кислить).
        # STRONG зелень (пользователь по A/B выбрал ПУНЧЕВЫЙ вариант, не «натуральный»
        # приглушённый — доверяем глазу, не тексту). Пунч/насыщенность лечат «плоскость».
        return ("colorbalance=rs=-0.03:gs=0.012:bs=0.05:rm=0.0:gm=-0.006:bm=0.004:"
                "rh=0.018:gh=0.0:bh=-0.012,"
                "selectivecolor=correction_method=relative:greens=0.07 0 -0.11 -0.03:"
                "yellows=0 0 -0.14 0.02:reds=-0.12 0.22 0.26 0:magentas=0 0.26 0.10 0,"
                "vibrance=intensity=0.26,eq=contrast=1.07")
    if klass == "neutral":
        return ("colorbalance=rs=-0.03:gs=0.006:bs=0.05:rh=0.016:gh=0.0:bh=-0.016,"
                "selectivecolor=correction_method=relative:reds=-0.12 0.22 0.26 0:"
                "magentas=0 0.26 0.10 0:yellows=0 0.05 0.10 0,"
                "vibrance=intensity=0.23,eq=contrast=1.07")
    return ("colorbalance=rs=-0.01:gs=0.0:bs=0.03:rm=0.01:bm=-0.008:"
            "rh=0.04:gh=0.01:bh=-0.036,"
            "selectivecolor=correction_method=relative:reds=-0.12 0.22 0.26 0:"
            "magentas=0 0.26 0.10 0:yellows=0 0.06 0.12 0,"
            "vibrance=intensity=0.25,eq=contrast=1.08")


def _extract(cfg, video, t, vf, out_jpg):
    ffmpeg = cfg["tools"]["ffmpeg"]
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
           "-i", video, "-frames:v", "1"]
    if vf:
        cmd += ["-vf", vf]
    cmd += [out_jpg]
    subprocess.run(cmd, capture_output=True)


def make_compare(cfg, preview, picks, out_jpg):
    """Кадр до / техническая / финал для выбранных планов."""
    import cv2
    scratch = os.path.join(os.path.dirname(out_jpg), "_cmp_tmp")
    os.makedirs(scratch, exist_ok=True)
    rows = []
    for (t, klass, tech, final) in picks:
        cells = []
        for tag, vf in (("BEFORE", None), (f"TECH ({klass})", tech), (f"FINAL ({klass})", final)):
            p = os.path.join(scratch, f"{int(t)}_{tag[:4]}.jpg")
            _extract(cfg, preview, t, vf, p)
            im = cv2.imread(p)
            if im is None:
                im = np.zeros((405, 720, 3), np.uint8)
            im = cv2.resize(im, (720, 405))
            cv2.rectangle(im, (0, 0), (720, 24), (0, 0, 0), -1)
            cv2.putText(im, f"{tag}  t={t:.0f}s", (6, 17), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1, cv2.LINE_AA)
            cells.append(im)
        rows.append(np.hstack(cells))
    cv2.imwrite(out_jpg, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])


def make_scopes(cfg, video, picks, out_jpg):
    """Панель контроля: кадр | RGB parade | vectorscope на green- и warm-плане ФИНАЛА.
    parade показывает цветовой перекос по каналам, vectorscope — насыщ/направление."""
    import cv2
    ffmpeg = cfg["tools"]["ffmpeg"]
    scratch = os.path.join(os.path.dirname(out_jpg), "_scope_tmp"); os.makedirs(scratch, exist_ok=True)
    rows = []
    for (t, klass) in picks:
        cells = []
        specs = [("FRAME " + klass, None),
                 ("RGB PARADE", "format=rgb24,waveform=mode=column:components=7:display=parade:graticule=green:filter=lowpass"),
                 ("VECTORSCOPE", "vectorscope=mode=color3:graticule=green:flags=name")]
        for tag, vf in specs:
            p = os.path.join(scratch, f"{int(t)}_{tag[:4]}.png")
            _extract(cfg, video, t, vf, p)
            im = cv2.imread(p)
            if im is None:
                im = np.zeros((480, 480, 3), np.uint8)
            im = cv2.resize(im, (480, 480))
            cv2.rectangle(im, (0, 0), (480, 22), (0, 0, 0), -1)
            cv2.putText(im, f"{tag}  t={t:.0f}s", (5, 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1, cv2.LINE_AA)
            cells.append(im)
        rows.append(np.hstack(cells))
    cv2.imwrite(out_jpg, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 4:
        print(__doc__); return
    preview, otio_path, out_mp4 = sys.argv[1], sys.argv[2], sys.argv[3]
    strength = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].replace('.','').isdigit() else 0.55
    look_override = None                    # --look night|green|warm|neutral: форс на весь рилс
    if "--look" in sys.argv:
        look_override = sys.argv[sys.argv.index("--look") + 1]
    cfg = load_cfg(); ffmpeg = cfg["tools"]["ffmpeg"]

    # источник границ планов: otio ИЛИ cut_plan.json (внешний бит-пайплайн — reel-local времена)
    if otio_path.endswith(".json"):
        import json as _json
        pl = _json.load(open(otio_path, encoding="utf-8")); a0 = pl["start_time"]
        raw_bounds = [(round(s["start_time"] - a0, 3), round(s["end_time"] - a0, 3)) for s in pl["shots"]]
        # cut_plan УЖЕ кадро-точный (склейки 5-17мс) — refine_bounds двигал бы границу
        # детектором сцен на ±1 кадр -> один кадр графится чужим планом = ВСПЫШКА на стыке.
        bounds, moved = raw_bounds, 0
        # ТОЧНЫЕ границы В КАДРАХ из cut_plan (как в сборке): round(время*fps) расходится
        # с кумулятивной суммой кадров на ±1 -> один кадр чужого плана = вспышка (урок 2026-08-02).
        _fbase = pl["shots"][0]["start_frame"]
        fbounds = [(s["start_frame"] - _fbase, s["end_frame"] - _fbase) for s in pl["shots"]]
    else:
        bounds, moved = refine_bounds(cfg, preview, shot_bounds_from_otio(otio_path))
        fbounds = None
    stats = [measure(cfg, preview, t0, t1) for (t0, t1) in bounds]
    ires = [measure_ire(cfg, preview, t0, t1) for (t0, t1) in bounds]
    rlumas = [measure_robust_luma(cfg, preview, t0, t1) for (t0, t1) in bounds]
    fogs = [measure_fog(cfg, preview, t0, t1) for (t0, t1) in bounds]
    overs = [measure_overcast(cfg, preview, t0, t1) for (t0, t1) in bounds]
    klasses = [classify(s) if s else "neutral" for s in stats]
    # авто-foggy: много ярких малонасыщенных пикселей (туман/облака) -> тяжёлое небо/лук
    klasses = ["foggy" if fg > 0.28 else k for fg, k in zip(fogs, klasses)]
    # overcast — ТОЛЬКО по флагу --overcast-auto (по умолчанию ВЫКЛ). Причина: бледно-серое
    # хазовое небо (напр. закатный reel01) метрически НЕ отличается от свинцовых туч (reel04),
    # авто давало ложные «свинцовые небеса» на закатных рилсах. Пользователь: reel01 без свинца.
    if "--overcast-auto" in sys.argv:
        klasses = ["overcast" if (ov > 0.5 and k not in ("foggy", "warm", "night")) else k
                   for ov, k in zip(overs, klasses)]
    # ручной форс overcast на конкретные планы (1-based): --force-overcast "6,7"
    if "--force-overcast" in sys.argv:
        fo = {int(x) for x in sys.argv[sys.argv.index("--force-overcast") + 1].split(",") if x.strip()}
        klasses = ["overcast" if (i + 1) in fo else k for i, k in enumerate(klasses)]
    # ручной форс тёплого контражура на планы (1-based): --warm-slots "13,14,15"
    if "--warm-slots" in sys.argv:
        ws = {int(x) for x in sys.argv[sys.argv.index("--warm-slots") + 1].split(",") if x.strip()}
        klasses = ["golden" if (i + 1) in ws else k for i, k in enumerate(klasses)]
    if look_override:
        klasses = [look_override] * len(klasses)

    def grp_ref(kind):
        ls = [rl for rl, k in zip(rlumas, klasses) if rl and k == kind]
        return float(np.median(ls)) if ls else 118.0
    ref = {k: grp_ref(k) for k in set(klasses)}
    from collections import Counter
    print("Планов:", len(bounds), "| типы:", dict(Counter(klasses)),
          "| эталон яркости:", {k: round(v) for k, v in ref.items()})

    parts, labels, compare_picks = [], [], []
    print("  план  тип      IRE тени/лес/света  флаги")
    _FPS = 30000 / 1001
    for i, ((t0, t1), st, kl, ir) in enumerate(zip(bounds, stats, klasses, ires)):
        # ПОКАДРОВЫЙ трим (не по секундам): float-границы включали/исключали 1 кадр
        # по-разному у соседних планов -> один кадр графился чужим планом = ВСПЫШКА на стыке.
        f0, f1 = fbounds[i] if fbounds else (round(t0 * _FPS), round(t1 * _FPS))
        chain = f"[0:v]trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS"
        if st is not None:
            shot_luma = rlumas[i] if rlumas[i] else st["luma"]
            tech = technical_chain(st, shot_luma, ref[kl], kl, ir, strength)
            if "--mobile" in sys.argv:
                _lift = float(sys.argv[sys.argv.index("--lift") + 1]) if "--lift" in sys.argv else 0.0
                look = look_chain_mobile(kl, (ir or {}).get("mid"), (ir or {}).get("hi"), _lift)
            elif "--v4" in sys.argv:
                look = look_chain_v4(kl, (ir or {}).get("mid"))
            else:
                look = look_chain(kl)
            chain += "," + tech + "," + look
            fl = ""
            if ir:
                if ir["lo"] < 5: fl += " лес<5!"
                if ir["hi"] > 95: fl += " облака>95!"
                print(f"  {i+1:2d}   {kl:7s} {ir['lo']:4.0f}/{ir['mid']:4.0f}/{ir['hi']:4.0f}      {fl or 'ok'}")
            # для сравнения берём один яркий green и один warm/neutral (середина плана)
            tmid = (t0 + t1) / 2
            if kl == "green" and not any(p[1] == "green" for p in compare_picks):
                compare_picks.append((tmid, kl, tech, tech + "," + look))
            if kl in ("warm", "neutral") and not any(p[1] in ("warm", "neutral") for p in compare_picks):
                compare_picks.append((tmid, kl, tech, tech + "," + look))
        chain += f"[s{i}]"
        parts.append(chain); labels.append(f"[s{i}]")

    # ПО-ФАЙЛОВЫЙ рендер + concat-демуксер (НЕ filter_complex concat): единый concat
    # вставлял ОДИН тёмный/светлый кадр на шве -> вспышка (урок 2026-08-02, гейт поймал сам).
    # СВОЙ tmp на каждый выход (2026-08-17): общий _grade_shots ловил гонку параллельных
    # грейдов (осиротевший ffmpeg держал g023 -> PermissionError у соседнего прогона).
    tag = os.path.splitext(os.path.basename(out_mp4))[0]
    tmpd = os.path.join(ROOT, "work", f"_grade_shots_{tag}"); os.makedirs(tmpd, exist_ok=True)
    for f in os.listdir(tmpd):
        try:
            os.remove(os.path.join(tmpd, f))
        except PermissionError:
            pass  # залоченный чужой хвост не роняет прогон: файл будет перезаписан
    gfiles = []
    ffprobe_p = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
    for i, ch in enumerate(parts):
        fcs = ch.rsplit("[", 1)[0] + "[o]"        # заменить хвостовой [si] на [o]
        gf = os.path.join(tmpd, f"g{i:03d}.mp4")
        exp = (fbounds[i][1] - fbounds[i][0]) if fbounds else None
        for attempt in (1, 2):
            rr = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", preview,
                                 "-filter_complex", fcs, "-map", "[o]", "-an", "-c:v", "libx264",
                                 "-preset", "medium", "-crf", "14", "-pix_fmt", "yuv420p",
                                 "-r", "30000/1001", gf], capture_output=True, text=True)
            pr = subprocess.run([ffprobe_p, "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=nb_frames", "-of",
                                 "default=nw=1:nk=1", gf], capture_output=True, text=True)
            try:
                got = int(pr.stdout.strip())
            except Exception:
                got = 0
            # ПРОВЕРКА КУСКА (2026-08-18): пустые хвостовые g-куски (сброс trim-счётчика
            # на смене параметров потока) молча обрезали фильм на 42с. rc==0 НЕ гарантия.
            # допуск 5 кадров (~0.17с): куски сборки легально гуляют на ±2, к последнему
            # слоту набегает 3+ (поймано на финале v4: 237/240) — это не обрезка.
            if rr.returncode == 0 and (exp is None and got > 0 or exp is not None and abs(got - exp) <= 5):
                break
            if attempt == 2:
                print(f"FATAL грейд: план {i+1} кадров {got}, ожидалось {exp}; "
                      f"stderr: {(rr.stderr or '')[-500:]}")
                sys.exit(2)
        gfiles.append(gf)
    lst = os.path.join(tmpd, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in gfiles:
            f.write(f"file '{p.replace(chr(92),'/')}'\n")
    r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", lst, "-i", preview,
                        "-map", "0:v", "-map", "1:a?", "-c:v", "copy", "-c:a", "copy",
                        "-shortest", out_mp4], capture_output=True, text=True)
    if r.returncode != 0:
        print("ffmpeg concat ERROR:", (r.stderr or "")[-1500:]); return
    print(f"Готово: {os.path.relpath(out_mp4, ROOT)}")

    if compare_picks and "--v4" not in sys.argv:   # v4-цепь с split-метками не влезает в -vf стилов
        cmp_path = os.path.splitext(out_mp4)[0] + "__COMPARE.jpg"
        make_compare(cfg, preview, compare_picks, cmp_path)
        print(f"Сравнение до/техн/финал: {os.path.relpath(cmp_path, ROOT)}")
        sc_path = os.path.splitext(out_mp4)[0] + "__SCOPES.jpg"
        make_scopes(cfg, out_mp4, [(t, kl) for (t, kl, _, _) in compare_picks], sc_path)
        print(f"Scopes (parade+vectorscope) финала: {os.path.relpath(sc_path, ROOT)}")


if __name__ == "__main__":
    main()
