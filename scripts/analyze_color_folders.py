# -*- coding: utf-8 -*-
"""
Разбор цветокора по папкам с обработанными фото (точка А).
- Считает цветовой профиль по каждой папке (тени/света, контраст, насыщенность, теплота).
- Собирает контактный лист (сетку превью) для визуального осмотра.
Не трогает исходники — пишет только в work/color_review/.
"""
import os, sys, glob, random
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "work", "color_review")
os.makedirs(OUT, exist_ok=True)


def imread_u(path):
    """cv2.imread с поддержкой не-ASCII путей (Windows)."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_u(path, img, params=None):
    ext = os.path.splitext(path)[1]
    ok, buf = cv2.imencode(ext, img, params or [])
    if ok:
        buf.tofile(path)
    return ok

FOLDERS = {
    "Italy_Slovenia_Hungary_2021": r"D:\FOTO\Обработанные\Италия. Словения. Венгрия 2021",
    "Turkey_Yachting_2024":        r"D:\FOTO\Обработанные\Турция. Яхтинг 2024",
    "Montenegro_NotYachting":      r"D:\FOTO\Обработанные\Черногория - обработанные\Не яхтинг",
}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


def list_imgs(folder):
    out = []
    for p in glob.glob(os.path.join(folder, "**", "*"), recursive=True):
        if p.lower().endswith(IMAGE_EXT):
            out.append(p)
    return sorted(out)


def stats(img):
    h, w = img.shape[:2]
    mx = 1000
    if max(h, w) > mx:
        s = mx / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)))
    bgr = img.astype(np.float32)
    b, g, r = bgr[..., 0], bgr[..., 1], bgr[..., 2]
    luma = 0.114 * b + 0.587 * g + 0.299 * r
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    # доля пересветов/провалов
    over = float((luma > 250).mean() * 100)
    under = float((luma < 5).mean() * 100)
    return {
        "shadows": float(np.percentile(luma, 25)),
        "highlights": float(np.percentile(luma, 75)),
        "contrast": float(luma.std()),
        "saturation": float(hsv[..., 1].mean()),
        "warmth": float(r.mean() - b.mean()),
        "over_pct": over,
        "under_pct": under,
        "mean_rgb": (float(r.mean()), float(g.mean()), float(b.mean())),
    }


def make_contact_sheet(paths, out_path, cols=5, rows=5, cell=320):
    random.seed(42)
    sample = paths if len(paths) <= cols * rows else random.sample(paths, cols * rows)
    sample = sorted(sample)
    sheet = np.full((rows * cell, cols * cell, 3), 30, np.uint8)
    for i, p in enumerate(sample):
        img = imread_u(p)
        if img is None:
            continue
        h, w = img.shape[:2]
        s = cell / max(h, w)
        rz = cv2.resize(img, (int(w * s), int(h * s)))
        rh, rw = rz.shape[:2]
        r_i, c_i = divmod(i, cols)
        y, x = r_i * cell + (cell - rh) // 2, c_i * cell + (cell - rw) // 2
        sheet[y:y + rh, x:x + rw] = rz
    imwrite_u(out_path, sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return len(sample)


def main():
    for tag, folder in FOLDERS.items():
        paths = list_imgs(folder)
        print(f"\n=== {tag} ({len(paths)} фото) ===")
        if not paths:
            print("  пусто/не найдено")
            continue
        # профиль по всем
        allstats = []
        for p in paths:
            img = imread_u(p)
            if img is not None:
                allstats.append(stats(img))
        if allstats:
            def avg(k): return float(np.mean([s[k] for s in allstats]))
            print(f"  тени(25%):     {avg('shadows'):.0f} / 255")
            print(f"  света(75%):    {avg('highlights'):.0f} / 255")
            print(f"  контраст(std): {avg('contrast'):.0f}")
            print(f"  насыщенность:  {avg('saturation'):.0f} / 255")
            print(f"  теплота(R-B):  {avg('warmth'):+.1f}")
            print(f"  пересветы:     {avg('over_pct'):.2f}%  провалы теней: {avg('under_pct'):.2f}%")
            mr = [avg_rgb for avg_rgb in (
                np.mean([s['mean_rgb'][0] for s in allstats]),
                np.mean([s['mean_rgb'][1] for s in allstats]),
                np.mean([s['mean_rgb'][2] for s in allstats]))]
            print(f"  средний RGB:   R{mr[0]:.0f} G{mr[1]:.0f} B{mr[2]:.0f}")
        sheet_path = os.path.join(OUT, f"sheet_{tag}.jpg")
        n = make_contact_sheet(paths, sheet_path)
        print(f"  контактный лист: {os.path.relpath(sheet_path, ROOT)} ({n} кадров)")


if __name__ == "__main__":
    main()
