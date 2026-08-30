# -*- coding: utf-8 -*-
"""
Экспорт монтажного таймлайна для DaVinci Resolve через OpenTimelineIO.

Зачем: ручной EDL хрупок (тайм-коды, дробные fps). OTIO строит таймлайн как объект
и выгружает в .otio / .edl (CMX3600) / .fcpxml — Resolve корректно импортирует любой.

API:
    build_timeline(segments, name, fps) -> otio.schema.Timeline
    export_timeline(timeline, out_base)  -> пишет .otio/.edl/.fcpxml рядом

segment = {
    "path": абсолютный путь к исходнику,
    "src_in": вход в исходнике, сек,
    "src_out": выход в исходнике, сек,
    "name": имя клипа (опц.),
}

Smoke-test (проверка, что выгрузка валидна): берёт первые клипы из work/clip_index.json,
режет по 3 c и пишет output/_smoke_timeline.* — это НЕ монтаж, а проверка экспорта.
"""
import os, sys, json
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import opentimelineio as otio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rt(seconds, fps):
    return otio.opentime.RationalTime(round(seconds * fps), fps)


def build_timeline(segments, name="timeline", fps=30.0, audio=None):
    """Собирает OTIO-таймлайн из списка сегментов (последовательные склейки).

    audio (опц.): {"path", "src_in", "src_out", "name"} — один музыкальный
    клип на A1, синхронный со сборкой (этап 3/4, музыкальная синхронизация).
    """
    timeline = otio.schema.Timeline(name=name)
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    for seg in segments:
        path = seg["path"]
        src_in = float(seg.get("src_in", 0.0))
        src_out = float(seg["src_out"])
        dur = max(1.0 / fps, src_out - src_in)
        url = "file://" + path.replace("\\", "/")
        # доступный диапазон медиа (для надёжности — от 0 до src_out)
        available = otio.opentime.TimeRange(
            start_time=_rt(0, fps), duration=_rt(src_out + 1, fps))
        media = otio.schema.ExternalReference(target_url=url, available_range=available)
        clip = otio.schema.Clip(
            name=seg.get("name") or os.path.splitext(os.path.basename(path))[0],
            media_reference=media,
            source_range=otio.opentime.TimeRange(
                start_time=_rt(src_in, fps), duration=_rt(dur, fps)),
        )
        track.append(clip)

    if audio:
        a_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        timeline.tracks.append(a_track)
        a_path = audio["path"]
        a_in = float(audio.get("src_in", 0.0))
        a_out = float(audio["src_out"])
        a_dur = max(1.0 / fps, a_out - a_in)
        a_url = "file://" + a_path.replace("\\", "/")
        a_available = otio.opentime.TimeRange(
            start_time=_rt(0, fps), duration=_rt(a_out + 1, fps))
        a_media = otio.schema.ExternalReference(target_url=a_url, available_range=a_available)
        a_clip = otio.schema.Clip(
            name=audio.get("name") or os.path.splitext(os.path.basename(a_path))[0],
            media_reference=a_media,
            source_range=otio.opentime.TimeRange(
                start_time=_rt(a_in, fps), duration=_rt(a_dur, fps)),
        )
        a_track.append(a_clip)
    return timeline


def export_timeline(timeline, out_base):
    """Пишет .otio / .edl / .fcpxml. Возвращает список созданных файлов."""
    os.makedirs(os.path.dirname(out_base), exist_ok=True)
    written = []
    # .otio — родной формат (Resolve 18+ импортирует напрямую)
    p = out_base + ".otio"
    otio.adapters.write_to_file(timeline, p)
    written.append(p)
    # .fcpxml — самый надёжный импорт в Resolve (Final Cut XML)
    try:
        p = out_base + ".fcpxml"
        otio.adapters.write_to_file(timeline, p, adapter_name="fcpx_xml")
        written.append(p)
    except Exception as e:
        print(f"  fcpxml: пропущен ({e})")
    # .edl — CMX3600, на случай если нужен простой EDL
    try:
        p = out_base + ".edl"
        otio.adapters.write_to_file(timeline, p, adapter_name="cmx_3600",
                                    rate=timeline.duration().rate)
        written.append(p)
    except Exception as e:
        print(f"  edl: пропущен ({e})")
    return written


def _smoke():
    idx_path = os.path.join(ROOT, "work", "clip_index.json")
    if not os.path.exists(idx_path):
        print("Нет work/clip_index.json — сначала запусти stage1_index.py"); return
    with open(idx_path, "r", encoding="utf-8") as f:
        idx = json.load(f)
    clips = [c for c in idx["clips"] if c.get("duration_sec", 0) >= 3][:6]
    fps = clips[0]["fps"] if clips else 30.0
    segments = [{"path": c["path"], "src_in": 1.0, "src_out": 4.0,
                 "name": c["file"]} for c in clips]
    tl = build_timeline(segments, name="smoke_timeline", fps=fps)
    out_base = os.path.join(ROOT, "output", "_smoke_timeline")
    written = export_timeline(tl, out_base)
    print(f"Smoke-test: {len(segments)} сегментов, fps={fps}")
    print(f"Длительность таймлайна: {tl.duration().to_seconds():.1f} c")
    for w in written:
        print(f"  записан: {os.path.relpath(w, ROOT)} ({os.path.getsize(w)} байт)")
    # обратная проверка: читаем .otio назад
    back = otio.adapters.read_from_file(out_base + ".otio")
    print(f"  обратное чтение OK: клипов={len(list(back.find_clips()))}")


if __name__ == "__main__":
    _smoke()
