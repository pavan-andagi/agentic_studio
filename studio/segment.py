"""Find the moments a clip changes, so each one can be swapped on its own.

A character swap over a whole clip asks one reference picture to stand
for every pose and every camera angle in it. When the subject rolls
over, turns to the camera or the shot pushes in, that picture is wrong
for most of the footage - and with no pose signal to follow, the model
invents a layout to fit the mask. A dog came back facing the wrong way
for exactly this reason.

So: cut the clip where it actually changes, give each piece its own
reference drawn from its own footage, and generate piece by piece.
This file does the first half - it finds the pieces.

    python -m studio.segment clip.mp4 --out seg/
    python -m studio.segment clip.mp4 --out seg/ --sensitivity 1.4

It writes segments.json, a full-resolution frame for each segment, and
a contact sheet to look at before anything expensive runs.

What it measures, and what it does not: colour and edge structure over a
grid, which moves when the subject moves, when the camera moves and when
the light changes. It knows nothing about anatomy - it cannot tell you
the dog stood up, only that the frame stopped looking like it did. That
is the right signal for this job and a poor one for any other.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

GRID = 6          # cells a side; 36 cells over the frame
ORIENT = 4        # edge-direction bins per cell
SMALL = 96        # analysis size - detail here is noise, not signal


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,nb_frames,width,height",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = (st["r_frame_rate"].split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    secs = float(d["format"]["duration"])
    frames = int(st.get("nb_frames") or round(secs * fps))
    return {"fps": fps, "seconds": secs, "frames": frames,
            "width": int(st["width"]), "height": int(st["height"])}


def thumbs(path, every, work):
    """Every Nth frame, small, as an array. One ffmpeg pass."""
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"select=not(mod(n\\,{every})),scale={SMALL}:{SMALL}",
         "-vsync", "0", str(work / "t%05d.png")], check=True)
    files = sorted(work.glob("t*.png"))
    if not files:
        sys.exit("ffmpeg produced no frames from that file")
    return [np.asarray(Image.open(f).convert("RGB"), dtype=np.float32) / 255
            for f in files]


def describe(img):
    """Colour and edge structure per cell, as one vector.

    Colour alone calls a lighting change a new shot and misses a turn of
    the head. Edge direction alone is blind to the subject moving across
    a plain background. Together they behave."""
    h, w, _ = img.shape
    grey = img.mean(axis=2)
    gy, gx = np.gradient(grey)
    mag = np.hypot(gx, gy)
    ang = (np.arctan2(gy, gx) + math.pi) % math.pi       # 0..pi, undirected
    binned = np.minimum((ang / math.pi * ORIENT).astype(int), ORIENT - 1)

    rows = np.array_split(np.arange(h), GRID)
    cols = np.array_split(np.arange(w), GRID)
    out = []
    for rs in rows:
        for cs in cols:
            cell = img[rs[0]:rs[-1] + 1, cs[0]:cs[-1] + 1]
            m = mag[rs[0]:rs[-1] + 1, cs[0]:cs[-1] + 1]
            b = binned[rs[0]:rs[-1] + 1, cs[0]:cs[-1] + 1]
            out.extend(cell.reshape(-1, 3).mean(axis=0))      # 3
            hist = np.zeros(ORIENT, dtype=np.float32)
            for k in range(ORIENT):
                hist[k] = m[b == k].sum()
            s = hist.sum()
            out.extend(hist / s if s > 1e-6 else hist)        # 4
            out.append(float(m.mean()))                       # 1
    v = np.asarray(out, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 1e-6 else v


def cut(vecs, min_len, sensitivity, max_len=0):
    """Walk the clip, holding an anchor, and cut when it stops matching.

    Anchor-to-frame rather than frame-to-frame: a slow turn moves a
    little per frame and a long way overall, and frame-to-frame never
    sees it. The threshold comes from the clip's own movement, because
    a locked-off shot and a handheld one disagree about what 'different'
    means by an order of magnitude."""
    steps = [float(np.linalg.norm(vecs[i] - vecs[i - 1]))
             for i in range(1, len(vecs))]
    if not steps:
        return [(0, 0)]
    typical = float(np.median(steps)) or 1e-6
    thresh = max(typical * 6.0 / sensitivity, 0.02)

    bounds, anchor, start = [], vecs[0], 0
    for i in range(1, len(vecs)):
        # A segment that runs long is cut anyway. Nothing in a still
        # passage says "new shot", and a model given eight seconds of
        # one chunk drifts away from its reference by the end of it.
        too_long = max_len and i - start >= max_len
        if too_long or (np.linalg.norm(vecs[i] - anchor) > thresh
                        and i - start >= min_len):
            bounds.append((start, i - 1))
            start, anchor = i, vecs[i]
    bounds.append((start, len(vecs) - 1))
    # A last sliver is a seam, not a segment.
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] + 1 < min_len:
        a, _ = bounds[-2]
        bounds[-2:] = [(a, bounds[-1][1])]
    return bounds


def medoid(vecs, lo, hi):
    """The frame that looks most like the rest of its own segment.

    The first frame of a segment is usually mid-transition - the worst
    possible thing to hand a model as 'what this part looks like'."""
    block = vecs[lo:hi + 1]
    if len(block) == 1:
        return lo
    d = [sum(float(np.linalg.norm(a - b)) for b in block) for a in block]
    return lo + int(np.argmin(d))


def wan_frames(n):
    """Wan wants 4n+1 frames. A segment that is not is a segment that
    gets silently truncated."""
    return max(5, n - ((n - 1) % 4))


def still(video, frame, out):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{frame})", "-vsync", "0",
         "-frames:v", "1", "-y", str(out)], check=True)


def sheet(images, out, cols=4, wide=1400):
    ims = [Image.open(p).convert("RGB") for p in images]
    if not ims:
        return
    cols = min(cols, len(ims))
    rows = math.ceil(len(ims) / cols)
    cw = wide // cols
    ch = int(cw * ims[0].height / ims[0].width)
    board = Image.new("RGB", (cw * cols, ch * rows), (14, 16, 22))
    for i, im in enumerate(ims):
        board.paste(im.resize((cw, ch)), ((i % cols) * cw, (i // cols) * ch))
    board.save(out, quality=92)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--out", default="segments")
    ap.add_argument("--sensitivity", type=float, default=1.0,
                    help="higher cuts more often (try 0.6 to 2.0)")
    ap.add_argument("--min-seconds", type=float, default=1.0,
                    help="no segment shorter than this; a swap needs "
                         "frames to work with")
    ap.add_argument("--max-seconds", type=float, default=4.0,
                    help="no segment longer than this either, however "
                         "still the footage is: a long chunk drifts "
                         "from its own reference. 0 for no limit")
    ap.add_argument("--every", type=int, default=0,
                    help="sample every Nth frame (default: about 8 a "
                         "second)")
    args = ap.parse_args()

    src = Path(args.video)
    if not src.exists():
        sys.exit(f"{src} does not exist")
    info = probe(src)
    every = args.every or max(1, round(info["fps"] / 8))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="seg."))
    try:
        imgs = thumbs(src, every, work)
    finally:
        pass
    vecs = [describe(i) for i in imgs]
    shutil.rmtree(work, ignore_errors=True)

    min_len = max(2, round(args.min_seconds * info["fps"] / every))
    max_len = (round(args.max_seconds * info["fps"] / every)
               if args.max_seconds else 0)
    bounds = cut(vecs, min_len, args.sensitivity, max_len)

    segs, shots = [], []
    for n, (lo, hi) in enumerate(bounds, 1):
        first, last = lo * every, min(hi * every + every - 1,
                                      info["frames"] - 1)
        rep = medoid(vecs, lo, hi) * every
        png = out / f"seg_{n:02d}.png"
        still(src, rep, png)
        shots.append(png)
        segs.append({
            "id": f"g{n:02d}",
            "first_frame": first, "last_frame": last,
            "frames": last - first + 1,
            "wan_frames": wan_frames(last - first + 1),
            "seconds": round((last - first + 1) / info["fps"], 2),
            "start": round(first / info["fps"], 2),
            "reference_frame": rep,
            "reference_image": str(png)})

    (out / "segments.json").write_text(json.dumps(
        {"video": str(src), **info, "sampled_every": every,
         "sensitivity": args.sensitivity, "segments": segs}, indent=2),
        encoding="utf-8")
    sheet(shots, out / "contact.jpg")

    print(f"\n  {src.name}: {info['frames']} frames, {info['fps']:.0f}fps, "
          f"{info['seconds']:.1f}s")
    print(f"  sampled every {every} frames -> {len(vecs)} looks\n")
    for s in segs:
        print(f"  {s['id']}  {s['start']:>5.1f}s  {s['seconds']:>4.1f}s  "
              f"frames {s['first_frame']:>4}-{s['last_frame']:<4} "
              f"(wan {s['wan_frames']:>3})  ref @{s['reference_frame']}")
    print(f"\n  {out}/contact.jpg  - look at this before anything else\n")


if __name__ == "__main__":
    main()
