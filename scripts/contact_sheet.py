# -*- coding: utf-8 -*-
"""Контактный лист по списку idx: mid-кадр каждого клипа -> сетка (cv2) с подписью idx.
Использование: python contact_sheet.py <out.jpg> <idx,idx,... | a-b> [cols]"""
import os, sys, json, subprocess, yaml, cv2
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_idx(s):
    out = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main():
    outp = sys.argv[1]; idxs = parse_idx(sys.argv[2]); cols = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    ff = cfg["tools"]["ffmpeg"]
    cat = json.load(open(os.path.join(ROOT, "work", "clip_catalog.json"), encoding="utf-8"))
    by = {c["idx"]: c for c in cat["clips"]}
    tmp = os.path.join(os.path.dirname(outp), "_cs"); os.makedirs(tmp, exist_ok=True)
    cell = 300; imgs = []
    for i in idxs:
        cl = by.get(i)
        im = np.zeros((cell, cell, 3), np.uint8)
        if cl:
            t = cl["duration_sec"] * 0.45
            p = os.path.join(tmp, f"{i}.jpg")
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.1f}",
                            "-i", cl["path"], "-frames:v", "1", p], capture_output=True)
            r = cv2.imread(p)
            if r is not None:
                r = cv2.resize(r, (cell, cell) if r.shape[0] == r.shape[1] else
                               (int(cell * r.shape[1] / r.shape[0]), cell))
                r = cv2.resize(r, (cell, cell))
                im = r
        flow = 0
        if cl:
            fl = [s["flow"] for s in cl["seconds"]] or [0]; flow = sum(fl) / len(fl)
        lab = f"{i}" + (f" {flow:.1f}" if cl else " -")
        cv2.putText(im, lab, (6, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5)
        cv2.putText(im, lab, (6, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        imgs.append(im)
    rows = -(-len(imgs) // cols)
    grid = np.zeros((rows * cell, cols * cell, 3), np.uint8)
    for k, im in enumerate(imgs):
        r, c = divmod(k, cols)
        grid[r*cell:(r+1)*cell, c*cell:(c+1)*cell] = im
    cv2.imwrite(outp, grid, [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f"{outp}  ({len(imgs)} клипов, {cols}x{rows})")
    for r in range(rows):
        print(" ", idxs[r*cols:(r+1)*cols])


if __name__ == "__main__":
    main()
