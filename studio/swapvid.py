"""Replace a character through a whole clip, one piece at a time.

    python -m studio.swapvid clip.mp4 --ref front.png --ref side.png \\
        --say "Replace the dog with the golden retriever" \\
        --point 0.30 0.25 --point 0.60 0.44

A single reference picture cannot stand for every pose and camera angle
in a clip, and with no pose signal the model invents a layout to fit the
mask - which is how a swap comes back facing the wrong way. So the clip
is cut where it actually changes, each piece gets a reference made from
its own footage, each piece is generated on its own, and the pieces are
blended back together.

Nine stages. Every one writes what it made and skips what is already
there, so a chunk that fails costs that chunk:

    1 normalise   one frame rate, one index to count against
    2 segment     where the clip changes            -> segments.json
    3 points      carry your marks to each segment  -> per-segment points
    4 recast      each segment's own frame, in the new character
    5 cut         frame-exact chunks, with overlap
    6 generate    Wan per chunk                     -> the expensive part
    7 check       what a machine can see is wrong
    8 join        cross-dissolve the overlaps
    9 audio       the original sound, untouched

`--dry-run` walks all of it without ComfyUI, using each chunk as its own
take. It proves the cutting, the blending and the arithmetic, which is
most of what goes wrong, and costs nothing.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw

from . import segment as seg
from .comfy import Comfy
from .recast import prompt_for

IMG = (".png", ".jpg", ".jpeg", ".webp")
MAX_FPS = 30


def run(cmd):
    subprocess.run(cmd, check=True)


def say(line):
    print(f"  {line}", flush=True)


# ---------------------------------------------------------------- 1
def normalise(src, work, cap=MAX_FPS):
    """One frame rate for the whole run.

    Wan writes the result at whatever rate it is told and consumes the
    clip frame by frame, so the two have to agree. Above `cap` the clip
    is resampled by dropping frames - never by slowing them, which is
    what made an earlier swap play at two thirds speed."""
    info = seg.probe(src)
    out = work / "source.mp4"
    if info["fps"] <= cap + 0.01:
        if not out.exists():
            shutil.copy(src, out)
        return out, info
    if not out.exists():
        say(f"{info['fps']:.0f}fps is more than we will generate; "
            f"resampling to {cap}")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-vf", f"fps={cap}", "-c:v", "libx264", "-crf", "12",
             "-pix_fmt", "yuv420p", "-an", str(out)])
    return out, seg.probe(out)


def pad_4n1(first, last, total, back_first=False):
    """Grow [first, last] to the next 4k+1 length Wan will accept.

    Grown backwards by preference, into footage the previous chunk
    already covers, so the extra frames are thrown away at the join
    instead of showing up as length nobody asked for."""
    n = last - first + 1
    extra = (4 - ((n - 1) % 4)) % 4
    if not extra:
        return first, last
    if back_first and last + extra <= total - 1:
        return first, last + extra
    if first - extra >= 0:
        return first - extra, last
    if last + extra <= total - 1:
        return first, last + extra
    return first, last - ((n - 1) % 4)        # nowhere to grow: trim


# ---------------------------------------------------------------- 3
def patch(img, x, y, r):
    h, w = img.shape[:2]
    x, y = int(round(x * w)), int(round(y * h))
    x0, y0 = max(0, x - r), max(0, y - r)
    return img[y0:y0 + 2 * r, x0:x0 + 2 * r]


def track(frames, points, r=14, reach=0.3):
    """Carry the points you clicked to every segment's own frame.

    The subject moves, so a point marked on frame 0 can be on the
    bedsheet by frame 150. Each point is matched forward by its
    surroundings: the patch around it is looked for near where it was
    last seen. Cheap, and wrong sometimes - which is what `--only` and
    the points you can type at the gate are for.

    Points come in as fractions of the frame and leave the same way."""
    out = [list(points)]
    cur = list(points)
    for nxt in frames[1:]:
        prev = frames[len(out) - 1]
        moved = []
        for x, y in zip(cur[0::2], cur[1::2]):
            tpl = patch(prev, x, y, r)
            if tpl.size == 0:
                moved += [x, y]
                continue
            h, w = nxt.shape[:2]
            cx, cy = int(round(x * w)), int(round(y * h))
            span = int(reach * min(h, w))
            best, bx, by = -2.0, x, y
            step = max(1, span // 12)
            for dy in range(-span, span + 1, step):
                for dx in range(-span, span + 1, step):
                    px = (cx + dx) / w
                    py = (cy + dy) / h
                    cand = patch(nxt, px, py, r)
                    if cand.shape != tpl.shape:
                        continue
                    a = tpl - tpl.mean()
                    b = cand - cand.mean()
                    den = np.sqrt((a * a).sum() * (b * b).sum())
                    if den < 1e-6:
                        continue
                    score = float((a * b).sum() / den)
                    if score > best:
                        best, bx, by = score, px, py
            moved += [round(bx, 4), round(by, 4)]
        out.append(moved)
        cur = moved
    return out


# ---------------------------------------------------------------- 5
def chunk(src, first, last, out):
    """Frames `first` to `last` inclusive, exactly those."""
    if out.exists():
        return out
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-vf", f"select=between(n\\,{first}\\,{last}),setpts=N/FRAME_RATE/TB",
         "-vsync", "0", "-c:v", "libx264", "-crf", "12",
         "-pix_fmt", "yuv420p", "-an", str(out)])
    return out


def frames_of(path, into):
    into.mkdir(parents=True, exist_ok=True)
    if not any(into.glob("f*.png")):
        run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
             "-vsync", "0", str(into / "f%05d.png")])
    return sorted(into.glob("f*.png"))


# ---------------------------------------------------------------- 7
def inspect(take, reference, want_frames):
    """What a machine can tell is wrong, before you are asked to look."""
    notes, ok = [], True
    info = seg.probe(take)
    if abs(info["frames"] - want_frames) > 1:
        notes.append(f"{info['frames']} frames, asked for {want_frames}")
        ok = False

    work = take.parent / f".{take.stem}_look"
    files = frames_of(take, work)
    if not files:
        return False, ["no frames came out"]
    pick = [files[0], files[len(files) // 2], files[-1]]
    arrs = [np.asarray(Image.open(f).convert("RGB").resize(
        (seg.SMALL, seg.SMALL)), dtype=np.float32) / 255 for f in pick]
    shutil.rmtree(work, ignore_errors=True)

    if max(a.mean() for a in arrs) < 0.04:
        notes.append("black")
        ok = False
    if float(np.abs(arrs[0] - arrs[-1]).mean()) < 0.004:
        notes.append("frozen - nothing moves")
        ok = False

    if reference and Path(reference).exists():
        ref = np.asarray(Image.open(reference).convert("RGB").resize(
            (seg.SMALL, seg.SMALL)), dtype=np.float32) / 255
        rv = seg.describe(ref)
        d_mid = float(np.linalg.norm(seg.describe(arrs[1]) - rv))
        d_first = float(np.linalg.norm(seg.describe(arrs[0]) - rv))
        notes.append(f"identity {d_mid:.3f}, opening {d_first:.3f}")
        # Tuned loose on purpose: this is here to catch a chunk that
        # came back as something else, not to grade the picture.
        if d_first > 0.75:
            notes.append("opening frame looks nothing like its reference")
            ok = False
    return ok, notes


# ---------------------------------------------------------------- 8
def join(takes, overlaps, out, fps, work):
    """Concatenate, dissolving across the frames the chunks share.

    Frame by frame rather than with ffmpeg's xfade: the overlap is
    known exactly in frames, and a filter graph that is off by one
    leaves a stutter nobody can find afterwards."""
    stage = work / "join"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    kept, n = [], 0
    for i, take in enumerate(takes):
        files = frames_of(take, work / f".f{i:02d}")
        over = overlaps[i] if i < len(overlaps) else 0
        if i and over:
            tail = kept[-over:]
            kept = kept[:-over]
            for k, (a, b) in enumerate(zip(tail, files[:over])):
                w = (k + 1) / (over + 1)
                blend = Image.blend(Image.open(a).convert("RGB"),
                                    Image.open(b).convert("RGB"), w)
                p = stage / f"b{n:05d}.png"
                blend.save(p)
                kept.append(p)
                n += 1
            files = files[over:]
        kept.extend(files)

    # One numbered sequence, not a concat list. The concat demuxer
    # re-times what it is given and quietly dropped five frames of 245
    # the first time this ran; an image sequence at a fixed rate is
    # exactly as long as the number of files in it.
    seq = stage / "seq"
    seq.mkdir(exist_ok=True)
    for i, f in enumerate(kept):
        dest = seq / f"{i:06d}.png"
        try:
            os.link(f, dest)
        except OSError:
            shutil.copy(f, dest)
    run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps}",
         "-i", str(seq / "%06d.png"), "-c:v", "libx264", "-crf", "17",
         "-pix_fmt", "yuv420p", "-r", f"{fps}", str(out)])
    return len(kept)


# ---------------------------------------------------------------- 9
def with_audio(video, source, out):
    has = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0",
         str(source)], capture_output=True, text=True).stdout.strip()
    if not has:
        shutil.copy(video, out)
        return False
    # No -shortest. A phone clip's audio track is routinely a little
    # shorter than its video, and -shortest then trims the picture to
    # match it: 245 frames went in and 240 came out, which is five
    # frames of the swap quietly thrown away at the end.
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(source),
         "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac",
         str(out)])
    return True


def swap_clip(comfy, cfg, clip, refs, out, say_text="", points=None,
              avoid=None, seed=777, sensitivity=1.0, min_seconds=1.0,
              max_seconds=4.0, overlap=8, only=None, dry_run=False,
              status=None):
    """The whole thing, as one call. Returns (final video, segments).

    `comfy` may be None when `dry_run` is set. `status` is called with
    a short line per stage, which is what the dashboard shows while
    this runs - a swap that takes twenty minutes and says nothing looks
    exactly like a swap that has hung."""
    def note(line):
        say(line)
        if status:
            status(line)

    src = Path(clip)
    out = Path(out)
    work = out / "work"
    for d in (out, work, out / "cast", out / "chunks", out / "takes"):
        d.mkdir(parents=True, exist_ok=True)
    pick = set(only or ())
    points = list(points or [])
    avoid = list(avoid or [])

    # 1 ----------------------------------------------------------------
    note("normalising the clip")
    norm, info = normalise(src, work)

    # 2 ----------------------------------------------------------------
    every = max(1, round(info["fps"] / 8))
    thumbs = seg.thumbs(norm, every, work / "thumbs")
    vecs = [seg.describe(t) for t in thumbs]
    bounds = seg.cut(vecs,
                     max(2, round(min_seconds * info["fps"] / every)),
                     sensitivity,
                     round(max_seconds * info["fps"] / every)
                     if max_seconds else 0)
    segs, last_end = [], -1
    for n, (lo, hi) in enumerate(bounds, 1):
        first = lo * every
        last = min(hi * every + every - 1, info["frames"] - 1)
        grab = max(0, first - overlap) if n > 1 else first
        grab, last = pad_4n1(grab, last, info["frames"])
        rep = seg.medoid(vecs, lo, hi) * every
        png = out / f"seg_{n:02d}.png"
        if not png.exists():
            seg.still(norm, rep, png)
        segs.append({"id": f"g{n:02d}", "first_frame": first,
                     "last_frame": last, "grab_from": grab,
                     "overlap": max(0, last_end - grab + 1),
                     "frames": last - grab + 1,
                     "wan_frames": last - grab + 1,
                     "seconds": round((last - first + 1) / info["fps"], 2),
                     "reference_frame": rep, "reference_image": str(png)})
        last_end = last
    if segs:
        tail = segs[-1]
        tail["grab_from"], tail["last_frame"] = pad_4n1(
            tail["grab_from"], info["frames"] - 1, info["frames"],
            back_first=True)
        tail["frames"] = tail["wan_frames"] = (
            tail["last_frame"] - tail["grab_from"] + 1)
        tail["overlap"] = max(0, (segs[-2]["last_frame"] if len(segs) > 1
                                  else -1) - tail["grab_from"] + 1)
    note(f"{len(segs)} segment(s) found")
    seg.sheet([Path(x["reference_image"]) for x in segs],
              out / "contact.jpg")

    # 3 ----------------------------------------------------------------
    if points:
        small = []
        for x in segs:
            f = work / f"rep_{x['id']}.png"
            if not f.exists():
                seg.still(norm, x["reference_frame"], f)
            small.append(np.asarray(Image.open(f).convert("RGB").resize(
                (192, int(192 * info["height"] / info["width"]))),
                dtype=np.float32) / 255)
        for x, carried in zip(segs, track(small, points)):
            x["points"] = carried
        if avoid:
            for x, carried in zip(segs, track(small, avoid)):
                x["avoid"] = carried
        marks = []
        for x in segs:
            f = work / f"rep_{x['id']}.png"
            dot = work / f"pt_{x['id']}.png"
            im = Image.open(f).convert("RGB")
            g = ImageDraw.Draw(im)
            for xs, ys, col in ((x["points"][0::2], x["points"][1::2],
                                 (60, 230, 140)),
                                (x.get("avoid", [])[0::2],
                                 x.get("avoid", [])[1::2],
                                 (240, 90, 90))):
                for fx, fy in zip(xs, ys):
                    cx, cy = fx * im.width, fy * im.height
                    g.ellipse([cx - 16, cy - 16, cx + 16, cy + 16],
                              outline=col, width=5)
                    g.line([cx - 26, cy, cx + 26, cy], fill=col, width=3)
                    g.line([cx, cy - 26, cx, cy + 26], fill=col, width=3)
            im.save(dot)
            marks.append(dot)
        seg.sheet(marks, out / "points.jpg")
        note(f"{len(points) // 2} mark(s) carried across the segments")

    # 4 ----------------------------------------------------------------
    if dry_run:
        for x in segs:
            x["cast_image"] = x["reference_image"]
    else:
        wf_edit = cfg["comfy"]["workflows"].get("edit")
        if not wf_edit:
            raise RuntimeError("config.yaml has no comfy.workflows.edit, "
                               "which a per-segment swap needs")
        up = [comfy.upload(r) for r in list(refs)[:2]]
        said = prompt_for(say_text, len(up))
        for i, x in enumerate(segs, 1):
            dest = out / "cast" / f"{x['id']}_cast.png"
            if dest.exists() and (not pick or x["id"] not in pick):
                x["cast_image"] = str(dest)
                continue
            note(f"recasting reference {i} of {len(segs)}")
            got = [f for f in comfy.run(wf_edit, {
                "@PROMPT": said, "@SEED": seed,
                "@IMAGE": comfy.upload(x["reference_image"]),
                "@IMAGE2": up[0] if up else None,
                "@IMAGE3": up[1] if len(up) > 1 else None,
                "@FILENAME": f"studio/recast_{x['id']}"},
                out / "cast", label=f"{x['id']} recast")
                if f.suffix.lower() in IMG]
            if not got:
                raise RuntimeError(f"{x['id']}: the edit workflow returned "
                                   f"no image")
            shutil.copy(got[0], dest)
            x["cast_image"] = str(dest)
        seg.sheet([Path(x["cast_image"]) for x in segs],
                  out / "contact_cast.jpg")

    # 5 ----------------------------------------------------------------
    for x in segs:
        x["chunk"] = str(chunk(norm, x["grab_from"], x["last_frame"],
                               out / "chunks" / f"{x['id']}.mp4"))

    # 6 ----------------------------------------------------------------
    wf_anim = cfg["comfy"]["workflows"].get("animate") if not dry_run else None
    for i, x in enumerate(segs, 1):
        take = out / "takes" / f"{x['id']}.mp4"
        if take.exists() and (not pick or x["id"] not in pick):
            x["take"] = str(take)
            continue
        if dry_run:
            shutil.copy(x["chunk"], take)
            x["take"] = str(take)
            continue
        note(f"generating segment {i} of {len(segs)} "
             f"({x['seconds']}s, {x['wan_frames']} frames)")
        vals = {"@VIDEO": comfy.upload(x["chunk"]),
                "@IMAGE": comfy.upload(x["cast_image"]),
                "@PROMPT": say_text or "Replace the main subject.",
                "@POINTS": json.dumps(_sam(x.get("points"), info)),
                "@WIDTH": info["width"], "@HEIGHT": info["height"],
                "@FRAMES": x["wan_frames"],
                "@FPS": int(round(info["fps"])), "@SEED": seed,
                "@FILENAME": f"studio/swap_{x['id']}"}
        if x.get("avoid"):
            vals["@NEGPOINTS"] = json.dumps(_sam(x["avoid"], info))
        got = [f for f in comfy.run(wf_anim, vals, out / "takes",
                                    label=f"{x['id']} swap")
               if f.suffix.lower() in (".mp4", ".webm")]
        got = [f for f in got if f.name != Path(x["chunk"]).name]
        if not got:
            raise RuntimeError(f"{x['id']}: the animate workflow returned "
                               f"no video")
        shutil.copy(got[0], take)
        x["take"] = str(take)

    # 7 ----------------------------------------------------------------
    bad = []
    for x in segs:
        ok, notes = inspect(Path(x["take"]), x.get("cast_image"),
                            x["wan_frames"])
        x["check"] = {"ok": ok, "notes": notes}
        say(f"{x['id']}  {'ok ' if ok else 'BAD'}  {'; '.join(notes)}")
        if not ok:
            bad.append(x["id"])
    if bad:
        note(f"flagged: {', '.join(bad)}")

    # 8, 9 --------------------------------------------------------------
    note("joining the segments")
    silent = work / "joined.mp4"
    total = join([Path(x["take"]) for x in segs],
                 [x["overlap"] for x in segs], silent, info["fps"], work)
    final = out / "swapped.mp4"
    with_audio(silent, src, final)

    (out / "segments.json").write_text(json.dumps(
        {"video": str(src), **info, "overlap": overlap, "seed": seed,
         "dry_run": dry_run, "frames_out": total, "flagged": bad,
         "segments": segs}, indent=2), encoding="utf-8")
    note(f"{total} frames out of {info['frames']}")
    return final, segs


def _sam(points, info):
    """Fractions -> the pixel coordinates SAM 2 wants."""
    if not points:
        return [{"x": info["width"] // 2, "y": info["height"] // 2}]
    return [{"x": int(round(x * info["width"])),
             "y": int(round(y * info["height"]))}
            for x, y in zip(points[0::2], points[1::2])]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--ref", action="append", default=[], required=True,
                    help="the replacement character; twice for two views")
    ap.add_argument("--say", default="",
                    help="e.g. 'Replace the dog with the golden retriever'")
    ap.add_argument("--point", action="append", nargs=2, type=float,
                    metavar=("X", "Y"), default=[],
                    help="where the character is in the FIRST frame, as "
                         "fractions. Give it more than once - head and "
                         "body - or the segmenter picks one part")
    ap.add_argument("--avoid", action="append", nargs=2, type=float,
                    metavar=("X", "Y"), default=[],
                    help="what is NOT the character")
    ap.add_argument("--out", default="swap")
    ap.add_argument("--sensitivity", type=float, default=1.0)
    ap.add_argument("--min-seconds", type=float, default=1.0)
    ap.add_argument("--max-seconds", type=float, default=4.0)
    ap.add_argument("--overlap", type=int, default=8,
                    help="frames shared between neighbours, dissolved "
                         "at the join")
    ap.add_argument("--seed", type=int, default=777)
    ap.add_argument("--only", default="", help="redo some segments: g03,g07")
    ap.add_argument("--dry-run", action="store_true",
                    help="no ComfyUI: each chunk stands in for its own "
                         "take, which still proves the cutting, the "
                         "blending and the arithmetic")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    src = Path(args.video)
    if not src.exists():
        sys.exit(f"{src} does not exist")
    refs = [Path(r) for r in args.ref][:2]
    for r in refs:
        if not r.exists():
            sys.exit(f"{r} does not exist")
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    comfy = None if args.dry_run else Comfy(
        cfg["comfy"].get("url", "http://127.0.0.1:8188"))

    t0 = time.time()
    try:
        final, segs = swap_clip(
            comfy, cfg, src, refs, Path(args.out), args.say,
            [v for xy in args.point for v in xy],
            [v for xy in args.avoid for v in xy],
            args.seed, args.sensitivity, args.min_seconds, args.max_seconds,
            args.overlap, {x.strip() for x in args.only.split(",")
                           if x.strip()}, args.dry_run)
    except RuntimeError as e:
        sys.exit(str(e))
    bad = [x["id"] for x in segs if not x["check"]["ok"]]
    print(f"\n  {final}   ({time.time() - t0:.0f}s)")
    if bad:
        print(f"  flagged: {', '.join(bad)}   redo:  --only "
              f"{','.join(bad)}\n")
    else:
        print()


if __name__ == "__main__":
    main()
