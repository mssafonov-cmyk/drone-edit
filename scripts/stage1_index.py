# -*- coding: utf-8 -*-
"""
Этап 1 — Индексация исходников + визуальный обзор.

- Сканирует текущую партию (project.footage_dir или input/).
- По каждому клипу: разрешение, fps, длительность, кодек, размер, битрейт.
- Собирает контактные листы (по одному кадру с середины клипа) для осмотра.
- Пишет work/clip_index.json и work/sheet_footage_NN.jpg.

Исходники только читаются. Запуск:
    & <python> scripts\\stage1_index.py
"""
import os, sys, json, glob, subprocess
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import yaml
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts")


def load_cfg():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def imwrite_u(path, img, params=None):
    ok, buf = cv2.imencode(os.path.splitext(path)[1], img, params or [])
    if ok:
        buf.tofile(path)
    return ok


def fourcc_str(cap):
    v = int(cap.get(cv2.CAP_PROP_FOURCC))
    return "".join([chr((v >> 8 * i) & 0xFF) for i in range(4)]).strip("\x00 ")


def ffprobe_meta(ffprobe, path):
    """Кодек и битрейт через ffprobe (надёжнее cv2 для контейнера)."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,bit_rate:format=bit_rate,duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        d = json.loads(out.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        fmt = d.get("format", {})
        br = st.get("bit_rate") or fmt.get("bit_rate")
        return st.get("codec_name"), (int(br) if br else None), \
            (float(fmt["duration"]) if fmt.get("duration") else None)
    except Exception:
        return None, None, None


def main():
    cfg = load_cfg()
    ffprobe = cfg["tools"]["ffprobe"]
    work = os.path.join(ROOT, cfg["paths"]["work"])
    os.makedirs(work, exist_ok=True)

    footage = (cfg.get("project", {}) or {}).get("footage_dir") or ""
    src_dir = footage if footage else os.path.join(ROOT, cfg["paths"]["input"])
    files = sorted(p for p in glob.glob(os.path.join(src_dir, "**", "*"), recursive=True)
                   if p.lower().endswith(VIDEO_EXT))

    print("=" * 70)
    print(f"ЭТАП 1 — Индексация: {src_dir}")
    print(f"Найдено клипов: {len(files)}")
    print("=" * 70)
    if not files:
        print("Нет видеофайлов."); return

    clips = []
    thumbs = []
    total_dur = 0.0
    for i, p in enumerate(files):
        cap = cv2.VideoCapture(p)
        if not cap.isOpened():
            print(f"  [{i}] не открыт: {os.path.basename(p)}"); continue
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = round(float(cap.get(cv2.CAP_PROP_FPS)), 3)
        nfr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fourcc = fourcc_str(cap)
        codec, bitrate, fdur = ffprobe_meta(ffprobe, p)
        dur = fdur if fdur else (nfr / fps if fps else 0)
        total_dur += dur or 0
        size = os.path.getsize(p)

        # кадр-превью с середины
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, nfr // 2))
        ok, fr = cap.read()
        if ok:
            thumbs.append((i, fr))
        cap.release()

        clips.append({
            "idx": i, "file": os.path.basename(p), "path": p,
            "w": w, "h": h, "fps": fps, "frames": nfr,
            "duration_sec": round(dur, 2), "codec": codec, "fourcc": fourcc,
            "bitrate_mbps": round(bitrate / 1e6, 1) if bitrate else None,
            "size_mb": round(size / 1e6, 1),
        })

    # --- агрегаты ---
    def dist(key):
        from collections import Counter
        return dict(Counter(c[key] for c in clips))
    res_dist = dist  # placeholder
    from collections import Counter
    res = Counter(f"{c['w']}x{c['h']}" for c in clips)
    fpsd = Counter(c["fps"] for c in clips)
    durs = np.array([c["duration_sec"] for c in clips])

    print(f"Суммарная длительность: {total_dur/60:.1f} мин ({total_dur:.0f} с)")
    print(f"Длина клипа: мин {durs.min():.1f}s / медиана {np.median(durs):.1f}s / макс {durs.max():.1f}s")
    print(f"Разрешения: {dict(res)}")
    print(f"FPS: {dict(fpsd)}")
    print(f"Кодеки: {dict(Counter(c['codec'] for c in clips))}")
    brs = [c['bitrate_mbps'] for c in clips if c['bitrate_mbps']]
    if brs:
        print(f"Битрейт: мин {min(brs)} / медиана {np.median(brs):.0f} / макс {max(brs)} Mbps")

    # --- контактные листы ---
    per_sheet, cols = 36, 6
    cell = 320
    sheets = 0
    for s in range(0, len(thumbs), per_sheet):
        chunk = thumbs[s:s + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = np.full((rows * cell, cols * cell, 3), 25, np.uint8)
        for j, (idx, fr) in enumerate(chunk):
            hh, ww = fr.shape[:2]
            sc = cell / max(hh, ww)
            rz = cv2.resize(fr, (int(ww * sc), int(hh * sc)))
            rh, rw = rz.shape[:2]
            r_i, c_i = divmod(j, cols)
            y, x = r_i * cell, c_i * cell + (cell - rw) // 2
            sheet[y:y + rh, x:x + rw] = rz
            cv2.putText(sheet, str(idx), (c_i * cell + 6, r_i * cell + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
        out = os.path.join(work, f"sheet_footage_{sheets:02d}.jpg")
        imwrite_u(out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(f"Контактный лист: {os.path.relpath(out, ROOT)} (клипы {chunk[0][0]}–{chunk[-1][0]})")
        sheets += 1

    out_json = os.path.join(work, "clip_index.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"source": src_dir, "count": len(clips),
                   "total_duration_sec": round(total_dur, 1), "clips": clips},
                  f, ensure_ascii=False, indent=2)
    print(f"\nИндекс -> {os.path.relpath(out_json, ROOT)}")


if __name__ == "__main__":
    main()
