"""Deterministic look filters applied after the clips are generated.

The point of this package is that nothing in it touches a model. Every
filter is a pure function of (frame, parameters), so a whole film can be
re-graded in seconds without regenerating a single plate. That is what
makes it worth iterating on a look at all.

A filter is an object with two methods:

    prime(frames)   optional, called once with a sample of frames, for
                    anything that must stay constant across the whole clip
                    (a colour palette is the obvious one - computing it per
                    frame makes the result flicker)
    apply(frame)    returns the transformed frame, uint8 HxWx3 RGB

Register one with @register("name"). The config names it:

    filters:
      - {name: quantise, colours: 24}
      - {name: pixelate, block: 6}
"""
import json
import subprocess
from pathlib import Path

import numpy as np

REGISTRY = {}


def register(name):
    def wrap(fn):
        REGISTRY[name] = fn
        return fn
    return wrap


def build(spec):
    """[{name: pixelate, block: 6}] -> [filter instance]"""
    out = []
    for item in spec or []:
        kw = dict(item)
        name = kw.pop("name", None)
        if name not in REGISTRY:
            raise ValueError(f"unknown filter {name!r}; "
                             f"have {sorted(REGISTRY)}")
        out.append(REGISTRY[name](**kw))
    return out


# --------------------------------------------------------------- video io
def probe(path):
    """(width, height, fps, has_audio) for a video file."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    streams = json.loads(out)["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    num, den = (v["r_frame_rate"].split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    audio = any(s["codec_type"] == "audio" for s in streams)
    return int(v["width"]), int(v["height"]), fps, audio


def read_frames(path, w, h):
    """Yield RGB frames. Streams, so a long clip never sits in memory."""
    size = w * h * 3
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def sample_frames(path, w, h, n=8):
    """A handful of frames spread through the clip, for prime()."""
    frames = list(read_frames(path, w, h))
    if not frames:
        return []
    step = max(1, len(frames) // n)
    return frames[::step][:n]


def apply_chain(src, dst, chain, crf=17):
    """Run `chain` over every frame of src and write dst.

    Audio is copied straight from the source, so LTX-generated audio
    survives the grade untouched."""
    src, dst = Path(src), Path(dst)
    w, h, fps, has_audio = probe(src)
    for f in chain:
        prime = getattr(f, "prime", None)
        if prime:
            prime(sample_frames(src, w, h))

    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-"]
    if has_audio:
        cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a",
                "-c:a", "copy", "-shortest"]
    cmd += ["-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(dst)]

    dst.parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    try:
        for frame in read_frames(src, w, h):
            for f in chain:
                frame = f.apply(frame)
            enc.stdin.write(np.ascontiguousarray(
                frame.astype(np.uint8)).tobytes())
            n += 1
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed writing {dst}")
    return n


def have_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"],
                           capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            return False
    return True


from . import builtin            # noqa: E402,F401  (populates REGISTRY)
