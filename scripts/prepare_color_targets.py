# -*- coding: utf-8 -*-
"""
Подготовка цветовых таргетов:
  1) Кураторская копия фото-референсов (точка А) -> color_targets/point_A_photo/
  2) Кадры-якорь премиум дрон-лука из референса Bali -> color_targets/target_drone/
  3) Печать цветового профиля по обоим ведрам.
Оригиналы в D:\\FOTO не трогаются — только копии. Анкер-кадры — для личного цвет-референса.
"""
import os, sys, glob, shutil
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CT = os.path.join(ROOT, "color_targets")
POINT_A = os.path.join(CT, "point_A_photo")
TARGET = os.path.join(CT, "target_drone")
os.makedirs(POINT_A, exist_ok=True)
os.makedirs(TARGET, exist_ok=True)

# из каких папок и сколько кадров взять в точку А (равномерно по списку)
PHOTO_SRC = {
    "Turkey":     (r"D:\FOTO\Обработанные\Турция. Яхтинг 2024", 5),
    "Montenegro": (r"D:\FOTO\Обработанные\Черногория - обработанные\Не яхтинг", 6),
    "Italy":      (r"D:\FOTO\Обработанные\Италия. Словения. Венгрия 2021", 5),
}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


def imread_u(path):
    try:
        return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_u(path, img, params=None):
    ok, buf = cv2.imencode(os.path.splitext(path)[1], img, params or [])
    if ok:
        buf.tofile(path)
    return ok


def list_imgs(folder):
    return sorted(p for p in glob.glob(os.path.join(folder, "**", "*"), recursive=True)
                  if p.lower().endswith(IMAGE_EXT))


def color_profile(images_bgr):
    sh, hi, ct, sat, wm = [], [], [], [], []
    for img in images_bgr:
        h, w = img.shape[:2]
        mx = 1000
        if max(h, w) > mx:
            s = mx / max(h, w); img = cv2.resize(img, (int(w*s), int(h*s)))
        bgr = img.astype(np.float32)
        b, g, r = bgr[..., 0], bgr[..., 1], bgr[..., 2]
        luma = 0.114*b + 0.587*g + 0.299*r
        sh.append(np.percentile(luma, 25)); hi.append(np.percentile(luma, 75))
        ct.append(luma.std())
        sat.append(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1].mean())
        wm.append(r.mean() - b.mean())
    return dict(shadows=round(float(np.mean(sh)),1), highlights=round(float(np.mean(hi)),1),
                contrast=round(float(np.mean(ct)),1), saturation=round(float(np.mean(sat)),1),
                warmth=round(float(np.mean(wm)),1), n=len(images_bgr))


def main():
    # --- 1) копия фото точки А ---
    copied = 0
    for tag, (folder, n) in PHOTO_SRC.items():
        imgs = list_imgs(folder)
        if not imgs:
            print(f"[{tag}] нет фото"); continue
        idxs = np.linspace(0, len(imgs)-1, min(n, len(imgs))).astype(int)
        for i in idxs:
            src = imgs[int(i)]
            dst = os.path.join(POINT_A, f"{tag}_{os.path.basename(src)}")
            shutil.copy2(src, dst)   # копия оригинала, без перекодирования
            copied += 1
    print(f"Точка А (фото): скопировано {copied} -> {os.path.relpath(POINT_A, ROOT)}")

    # --- 2) кадры-якорь дрон-лука из Bali ---
    bali = next((p for p in glob.glob(os.path.join(ROOT, "references", "*Bali*")) ), None)
    if bali:
        cap = cv2.VideoCapture(bali)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        grabbed = 0
        for k in range(8):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total*(k+0.5)/8))
            ok, fr = cap.read()
            if ok:
                imwrite_u(os.path.join(TARGET, f"bali_anchor_{k:02d}.jpg"), fr,
                          [cv2.IMWRITE_JPEG_QUALITY, 92]); grabbed += 1
        cap.release()
        print(f"Целевой дрон-лук (якорь Bali): {grabbed} кадров -> {os.path.relpath(TARGET, ROOT)}")
    else:
        print("Bali-референс не найден — якорь дрон-лука не создан")

    # --- 3) профили ---
    pa = [imread_u(p) for p in list_imgs(POINT_A)]
    pa = [x for x in pa if x is not None]
    td = [imread_u(p) for p in list_imgs(TARGET)]
    td = [x for x in td if x is not None]
    print("\n--- ПРОФИЛЬ: точка А (фото) ---")
    print(" ", color_profile(pa) if pa else "нет данных")
    print("--- ПРОФИЛЬ: целевой дрон-лук (якорь Bali) ---")
    print(" ", color_profile(td) if td else "нет данных")


if __name__ == "__main__":
    main()
