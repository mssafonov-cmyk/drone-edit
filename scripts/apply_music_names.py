# -*- coding: utf-8 -*-
"""Пост-проход: правило «трек+старт в имени файла» (IG глушит музыку — юзер переналоживает).
Переименовывает файлы в шаре: <имя> [Artist - Track @ m-ss].mp4 и вписывает строку МУЗЫКА
первой строкой в _caption.txt. Карта: work/music_starts.json (истина = cut_plan.start_time)."""
import os, sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M = {m["name"]: m for m in json.load(open(os.path.join(ROOT, "work", "music_starts.json"), encoding="utf-8"))}
# имя-в-шаре -> ключ карты + сайдкар локальный
PLAN = [
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "reel01_ninhbinh_FINAL_ГОТОВ_MOB.mp4", "reel01_ninhbinh", "output/Vietnam/reel01_ninhbinh_FINAL_caption.txt"),
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "reel01_ninhbinh_FAST2_ГОТОВ_MOB.mp4", "reel01_FAST2", None),
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "reel02_halong_FINAL_ГОТОВ_MOB.mp4", "reel02_halong", "output/Vietnam/reel02_halong_FINAL_caption.txt"),
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "reel03_sapa_FINAL_ГОТОВ_MOB.mp4", "reel03_sapa", "output/Vietnam/reel03_sapa_FINAL_caption.txt"),
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "reel04_mucangchai_FINAL_ГОТОВ_MOB.mp4", "reel04_mucangchai", "output/Vietnam/reel04_mucangchai_FINAL_caption.txt"),
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "reel05_garrya_FINAL_ГОТОВ_MOB.mp4", "reel05_garrya", "output/Vietnam/reel05_garrya_FINAL_caption.txt"),
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "reel06_waterfalls_FINAL_ГОТОВ_MOB.mp4", "reel06_waterfalls", "output/Vietnam/reel06_waterfalls_FINAL_caption.txt"),
 ("Z:/Вьетнам - дрон (cinematic reels + film)", "longform_vietnam_FINAL_ГОТОВ_MOB.mp4", "longform_vietnam", "output/Vietnam/longform_vietnam_FINAL_caption.txt"),
 ("Z:/Сейшелы - дрон", "reel_seychelles_ПРЕМИУМ_ГОТОВ.mp4", "sey_premium", "output/Seychelles/reel_seychelles_FINAL_caption.txt"),
 ("Z:/Сейшелы - дрон", "reel_sunset_ЗАКАТ_ГОТОВ.mp4", "sey_sunset", "output/Seychelles/reel_sunset_FINAL_caption.txt"),
 ("Z:/Сейшелы - дрон", "film_seychelles_16x9_ГОТОВ.mp4", "sey_film", "output/Seychelles/film_seychelles_FINAL_caption.txt"),
 ("Z:/Гонконг - дрон", "longform_hongkong_ГОТОВ_90с.mp4", "hk_film", "output/Hongkong/longform_hongkong_FINAL_caption.txt"),
 ("Z:/Гонконг - дрон", "reel_hk_night_ГОТОВ.mp4", "hk_night", "output/Hongkong/reel_hk_night_FINAL_caption.txt"),
]
for d, fname, key, cap in PLAN:
    m = M.get(key)
    if not m: continue
    tag = f"{m['track']} @ {m['start']}".replace(":", "-")
    src = os.path.join(d, fname)
    if os.path.exists(src):
        base, ext = os.path.splitext(fname)
        dst = os.path.join(d, f"{base} [{tag}]{ext}")
        # убрать прежние размеченные версии
        for f in os.listdir(d):
            if f.startswith(base + " [") and f.endswith(ext):
                os.remove(os.path.join(d, f))
        os.replace(src, dst)
        print(f"OK {os.path.basename(dst)}")
    line = f"МУЗЫКА (IG заглушит — наложить с этой секунды): {m['track']} @ {m['start']}\n\n"
    for capp in ([cap] if cap else []):
        if capp and os.path.exists(capp):
            t = open(capp, encoding="utf-8").read()
            if "МУЗЫКА (" not in t:
                open(capp, "w", encoding="utf-8").write(line + t)
            # обновить копию в шаре
            shc = os.path.join(d, os.path.basename(capp))
            if os.path.exists(shc):
                open(shc, "w", encoding="utf-8").write(line + t if "МУЗЫКА (" not in t else t)
print("APPLY_MUSIC_DONE")
