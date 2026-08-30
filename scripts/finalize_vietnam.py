# -*- coding: utf-8 -*-
"""
Финализация вьетнамской партии: вжигает заголовок локации на v3-грейды и пишет
мультиплатформенный сайдкар (через burn_captions.py) -> output/Vietnam/FINAL_*_graded.mp4.
Тексты/цитаты — те же, что были одобрены ранее (из старых caption.txt). Источник —
новые v3-грейды (output/color_preview/vietnam/), т.е. заменяют старый grade A/B/C.
Запуск: python finalize_vietnam.py
"""
import os, sys, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VD = os.path.join(ROOT, "output", "color_preview", "vietnam")
OUT = os.path.join(ROOT, "output", "Vietnam")
PY = sys.executable
BURN = os.path.join(ROOT, "scripts", "burn_captions.py")

ITEMS = [
    dict(src="reel01_ninhbinh_karst__ianasher__v3.mp4", out="FINAL_reel01_ninhbinh_graded.mp4",
         loc="N I N H  B I N H", country="V I E T N A M", loctext="Ninh Binh", author="NIETZSCHE",
         quote="He who climbs upon the highest mountains laughs at all tragedies, real or imaginary.",
         quote_ru="Тот, кто взошёл на высочайшие вершины, смеётся над всеми трагедиями — подлинными и мнимыми.",
         tags="ninhbinh,vietnamtravel,dronefilm,karstlandscape,hangmua"),
    dict(src="reel02_halong_sunset__benbhmer__v3night.mp4", out="FINAL_reel02_halong_graded.mp4",
         loc="H A  L O N G", country="V I E T N A M", loctext="Ha Long Bay", author="LAO TZU",
         quote="Nothing in the world is as soft and yielding as water. Yet for dissolving the hard and inflexible, nothing can surpass it.",
         quote_ru="Нет в мире ничего мягче и податливее воды, но в преодолении твёрдого и крепкого ей нет равных.",
         tags="halongbay,vietnamtravel,dronefilm,sunsetvibes,karstislands"),
    dict(src="reel03_foggy_mountains__ryx__v3.mp4", out="FINAL_reel03_sapa_graded.mp4",
         loc="S A P A", country="V I E T N A M", loctext="Sapa", author="LI BAI",
         quote="The birds have vanished into the sky, and now the last cloud drains away. We sit together, the mountain and me, until only the mountain remains.",
         quote_ru="Птицы растаяли в небе, и последнее облако ушло. Мы сидим вдвоём — гора и я, — пока не остаётся лишь гора.",
         tags="sapa,vietnamtravel,dronefilm,waterfall,mountainmist"),
    dict(src="reel04_mucangchai_terraces__khruangbin__v3.mp4", out="FINAL_reel04_mucangchai_graded.mp4",
         loc="M U  C A N G  C H A I", country="V I E T N A M", loctext="Mu Cang Chai", author="CONFUCIUS",
         quote="The man who moves a mountain begins by carrying away small stones.",
         quote_ru="Тот, кто сдвигает гору, начинает с того, что уносит маленькие камни.",
         tags="mucangchai,vietnamtravel,dronefilm,riceterraces,northvietnam"),
    dict(src="reel05_ecolodge__aleximurdoch__v3.mp4", out="FINAL_reel05_garrya_graded.mp4",
         loc="G A R R Y A", country="M U  C A N G  C H A I", loctext="Garrya Mù Cang Chải", author="ECKHART TOLLE",
         quote="Wherever you are, be there totally.",
         quote_ru="Где бы ты ни был — будь там всецело.",
         tags="garryamucangchai,mucangchai,vietnamtravel,ecolodge,riceterraces"),
    dict(src="reel06_waterfalls__m83__v3.mp4", out="FINAL_reel06_waterfalls_graded.mp4",
         loc="O  Q U Y  H O", country="V I E T N A M", loctext="O Quy Ho Pass", author="RUMI",
         quote="When you do things from your soul, you feel a river moving in you, a joy.",
         quote_ru="Когда делаешь от души, чувствуешь, как внутри тебя течёт река — радость.",
         tags="sapa,oquyho,vietnamtravel,waterfall,dronefilm"),
    dict(src="longform_vietnam__v3.mp4", out="FINAL_longform_vietnam_graded.mp4",
         loc="V I E T N A M", country="", loctext="Vietnam", author="",
         quote="Sometimes you find yourself in the middle of nowhere, and sometimes in the middle of nowhere you find yourself.",
         quote_ru="Иногда ты оказываешься посреди нигде — а иногда посреди нигде ты обретаешь себя.",
         tags="vietnam,vietnamtravel,dronefilm,cinematic,northvietnam"),
]

def _run():
    os.makedirs(OUT, exist_ok=True)
    flt = sys.argv[1] if len(sys.argv) > 1 else None   # опц. фильтр: обработать только совпавшие по имени
    for it in ITEMS:
        if flt and flt not in it["out"] and flt not in it["src"]:
            continue
        src = os.path.join(VD, it["src"])
        if not os.path.exists(src):
            print("ПРОПУСК (нет источника):", it["src"]); continue
        out = os.path.join(OUT, it["out"])
        cmd = [PY, BURN, src, out, "--location", it["loc"], "--country", it["country"],
               "--location-text", it["loctext"], "--quote", it["quote"], "--author", it["author"],
               "--quote-ru", it["quote_ru"], "--tags", it["tags"]]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(("OK  " if r.returncode == 0 else "ERR ") + it["out"])
        if r.returncode != 0:
            print((r.stderr or r.stdout or "")[-500:])
    print("FINALIZE DONE")


if __name__ == "__main__":
    _run()
