# -*- coding: utf-8 -*-
"""
Вертикальный кроп 16:9 -> 9:16 с трекингом точки интереса (правило 9), не по
центру, + гашение дрожания камеры (стабилизация) + опциональный лёгкий зум
(punch-in) для почти статичных планов.

Метод (без готовых моделей детекции — только numpy/opencv, что уже в проекте):
  1. Для каждого сэмплированного кадра считаем карту "интересности" — локальный
     контраст (Sobel) + отклонение цвета от медианного фона кадра (небо/вода
     однородны и предсказуемы, лодка/остров/горизонт — выделяются). X-центр
     масс этой карты по столбцам -> "куда сейчас смотрит" кроп. EMA-сглаживание
     (config.yaml -> edit.vertical_crop.smoothing).
  2. ОТДЕЛЬНО — фазовая корреляция между соседними кадрами -> накопленное
     смещение камеры. Низкочастотная часть этой траектории (скользящее
     среднее) = осознанное движение дрона, высокочастотная = дрожь/гимбал.
     Вычитаем только высокочастотную часть из позиции кропа — так гасим
     тряску, не трогая настоящее движение камеры. Это добавили после разбора:
     кроп по интересности сам по себе не гасит тряску исходника — просто
     двигает стабильное окно по нестабильной картинке.
  3. Опциональный punch-in (--zoom) — крoп сужается линейно за окно, создаёт
     ощущение движения на почти статичных планах (альтернатива speedup).
  4. Кроп применяется покадровой перекодировкой (не ffmpeg-выражением).

Траектория (X + величина гашёной тряски) сохраняется в work/tracked_crops/ —
для повтора вручную в Resolve (EDL это не переносит).
"""
import os, subprocess
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _probe_size(ffprobe, path):
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h = map(int, r.stdout.strip().split(","))
    return w, h


def _analyze(ffmpeg, path, t_start, dur, sample_fps=10, probe_w=480, probe_h=270):
    """За один проход декодирования считаем и X-интерес, и дрожание (фазовая
    корреляция с предыдущим кадром) — экономим второй decode-проход."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-ss", f"{t_start:.3f}", "-t", f"{dur:.3f}", "-i", path,
           "-vf", f"fps={sample_fps},scale={probe_w}:{probe_h}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
    frame_bytes = probe_w * probe_h * 3
    ts, interest_x, jitter_dx = [], [], []
    prev_gray = None
    k = 0
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        img = np.frombuffer(buf, np.uint8).reshape(probe_h, probe_w, 3)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)

        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        edge = np.sqrt(gx ** 2 + gy ** 2)
        med_color = np.median(img.reshape(-1, 3), axis=0)
        color_dev = np.linalg.norm(img.astype(np.float32) - med_color, axis=2)
        interest = np.clip(0.6 * edge + 0.4 * color_dev, 0, None)
        col_weight = interest.sum(axis=0)
        if col_weight.sum() < 1e-6:
            x_frac = 0.5
        else:
            idx = np.arange(probe_w)
            x_frac = float((idx * col_weight).sum() / col_weight.sum() / probe_w)

        dx = 0.0
        if prev_gray is not None:
            (dx, _dy), _resp = cv2.phaseCorrelate(prev_gray, gray)
        prev_gray = gray

        ts.append(k / sample_fps)
        interest_x.append(x_frac)
        jitter_dx.append(dx)
        k += 1
    proc.stdout.close()
    proc.wait()
    return ts, interest_x, jitter_dx, probe_w


def _ema(values, smoothing):
    """smoothing=0.85 -> alpha=0.15 (медленно, плавно едет за интересом)."""
    if not values:
        return values
    alpha = 1.0 - smoothing
    out = [values[0]]
    for x in values[1:]:
        out.append(out[-1] + alpha * (x - out[-1]))
    return out


def _moving_average(values, window):
    if window <= 1 or len(values) < 3:
        return list(values)
    arr = np.array(values, dtype=np.float32)
    window = min(window, len(values) - (1 - len(values) % 2))
    if window < 3:
        return list(values)
    kernel = np.ones(window) / window
    pad = window // 2
    padded = np.pad(arr, (pad, pad), mode="edge")
    return list(np.convolve(padded, kernel, mode="valid")[:len(values)])


def render_fixed_crop(cfg, path, t_start, dur, out_w, out_h, out_path):
    """ФИКСИРОВАННЫЙ кроп 16:9->9:16: один X-центр (медиана интересности) на весь план,
    окно НЕ двигается покадрово -> НЕТ дрожи «пиксель влево-вправо» (урок 2026-08-01:
    трекинг-кроп пересчитывал cx каждый кадр -> микро-джиттер на всех горизонталях).
    Один проход ffmpeg. Для спокойного материала субъект и так почти неподвижен."""
    ffmpeg, ffprobe = cfg["tools"]["ffmpeg"], cfg["tools"]["ffprobe"]
    src_w, src_h = _probe_size(ffprobe, path)
    crop_w = min(src_w, int(round(src_h * out_w / out_h)))
    crop_h = min(src_h, int(round(crop_w * out_h / out_w)))
    ts, interest_x, jitter_dx, probe_w = _analyze(ffmpeg, path, t_start, dur)
    x_frac = float(np.median(interest_x)) if interest_x else 0.5
    x_frac = min(0.98, max(0.02, x_frac))
    x0 = max(0, min(src_w - crop_w, int(x_frac * src_w) - crop_w // 2))
    y0 = max(0, (src_h - crop_h) // 2)
    vf = f"crop={crop_w}:{crop_h}:{x0}:{y0},scale={out_w}:{out_h}"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{t_start:.3f}", "-t", f"{dur:.3f}", "-i", path, "-vf", vf,
                    "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-r", "30000/1001", out_path], capture_output=True)
    return out_path


def render_tracked_crop(cfg, path, t_start, dur, out_w, out_h, out_path,
                        save_track_as=None, zoom=0.0):
    """Кроп 16:9->9:16 с трекингом + гашением дрожи, покадровая перекодировка.
    zoom: 0..~0.2 — лёгкий punch-in за длительность окна (0 = выкл).
    Возвращает out_path при успехе, None при ошибке."""
    ffmpeg, ffprobe = cfg["tools"]["ffmpeg"], cfg["tools"]["ffprobe"]
    smoothing = cfg["edit"]["vertical_crop"]["smoothing"]
    src_w, src_h = _probe_size(ffprobe, path)
    base_crop_w = min(src_w, int(round(src_h * out_w / out_h)))

    sample_fps = 10
    ts, interest_x, jitter_dx, probe_w = _analyze(ffmpeg, path, t_start, dur, sample_fps=sample_fps)
    interest_smooth = _ema(interest_x, smoothing)

    # дрожь: накопленное смещение (интеграл фазовой корреляции), низкие частоты
    # (скользящее среднее ~0.4с) — осознанное движение, вычитаем только разницу
    cum_dx = list(np.cumsum(jitter_dx)) if jitter_dx else []
    window = max(3, int(round(sample_fps * 0.4)))
    cum_smooth = _moving_average(cum_dx, window) if cum_dx else []
    jitter_component = ([c - s for c, s in zip(cum_dx, cum_smooth)] if cum_dx else [])
    # в долях ширины probe-кадра -> корректируем x-позицию (гасим только дрожь)
    corrected_x = [ix - (jc / probe_w) for ix, jc in zip(interest_smooth, jitter_component or [0] * len(interest_smooth))]

    if save_track_as:
        os.makedirs(os.path.dirname(save_track_as), exist_ok=True)
        with open(save_track_as, "w", encoding="utf-8") as f:
            f.write("t_sec,x_frac,jitter_removed_px\n")
            for i, t in enumerate(ts):
                jc = jitter_component[i] if i < len(jitter_component) else 0.0
                f.write(f"{t:.3f},{corrected_x[i]:.4f},{jc:.2f}\n")

    track_t = ts or [0.0]
    track_x = corrected_x or [0.5]

    fps = 29.97  # нативный fps исходников партии — иначе 30/29.97 даёт статтер
    cmd_in = [ffmpeg, "-hide_banner", "-loglevel", "error",
              "-ss", f"{t_start:.3f}", "-t", f"{dur:.3f}", "-i", path,
              "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc_in = subprocess.Popen(cmd_in, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 8)
    frame_bytes = src_w * src_h * 3

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd_out = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_w}x{out_h}",
               "-r", str(fps), "-i", "pipe:0",
               "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
               out_path]
    proc_out = subprocess.Popen(cmd_out, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    k = 0
    wrote_any = False
    n_frames_est = max(1, int(dur * fps))
    while True:
        buf = proc_in.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        img = np.frombuffer(buf, np.uint8).reshape(src_h, src_w, 3)
        t = k / fps
        x_frac = float(np.interp(t, track_t, track_x))
        x_frac = min(0.98, max(0.02, x_frac))

        crop_w = base_crop_w
        if zoom > 0:
            progress = min(1.0, k / n_frames_est)
            crop_w = int(round(base_crop_w * (1.0 - zoom * progress)))
            crop_w = max(int(out_w * 0.5), crop_w)
        crop_h = int(round(crop_w * out_h / out_w))
        crop_h = min(crop_h, src_h)
        crop_w = min(crop_w, src_w)

        cx = int(x_frac * src_w)
        x0 = max(0, min(src_w - crop_w, cx - crop_w // 2))
        y0 = max(0, (src_h - crop_h) // 2)
        cropped = img[y0:y0 + crop_h, x0:x0 + crop_w]
        resized = cv2.resize(cropped, (out_w, out_h))
        proc_out.stdin.write(resized.tobytes())
        wrote_any = True
        k += 1
    proc_in.stdout.close()
    proc_in.wait()
    proc_out.stdin.close()
    proc_out.wait()
    return out_path if wrote_any else None
