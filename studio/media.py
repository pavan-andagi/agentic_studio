"""ffmpeg / Pillow helpers: validation, contact sheets, assembly."""
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path):
    out = sh(["ffprobe", "-v", "error", "-show_entries",
              "stream=codec_type,width,height,r_frame_rate:format=duration",
              "-of", "json", str(path)]).stdout
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    num, den = v["r_frame_rate"].split("/")
    return {"width": v["width"], "height": v["height"],
            "fps": round(int(num) / int(den), 2),
            "duration": round(float(d["format"]["duration"]), 2),
            "audio": any(s["codec_type"] == "audio" for s in d["streams"])}


def validate(path):
    """The same checks as check_video.sh: black, frozen and jumpy frames."""
    p = str(path)
    black = sh(["ffmpeg", "-i", p, "-vf", "blackdetect=d=0.1:pix_th=0.10",
                "-an", "-f", "null", "-"]).stderr.count("black_start")
    freeze = re.findall(r"freeze_duration: ([\d.]+)", sh(
        ["ffmpeg", "-i", p, "-vf", "freezedetect=n=0.003:d=0.5",
         "-an", "-f", "null", "-"]).stderr)
    jumps = re.findall(r"pts_time:([\d.]+)", sh(
        ["ffmpeg", "-i", p, "-vf", "select='gt(scene,0.4)',showinfo",
         "-an", "-f", "null", "-"]).stderr)
    info = probe(path)
    issues = []
    if black:
        issues.append(f"{black} black segment(s)")
    if freeze:
        issues.append("frozen for " + ", ".join(f"{f}s" for f in freeze))
    if jumps:
        issues.append("sudden jumps at " + ", ".join(f"{j}s" for j in jumps))
    return info, issues


def contact_sheet(video, out_png, frames=8):
    d = probe(video)["duration"]
    sh(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
        f"fps={frames}/{d},scale=320:-1,tile={frames}x1",
        "-frames:v", "1", str(out_png)])
    return out_png


def image_grid(paths, out_png, thumb_h=480):
    """Numbered side-by-side grid of candidate images for picking."""
    ims = [Image.open(p).convert("RGB") for p in paths]
    ims = [im.resize((int(im.width * thumb_h / im.height), thumb_h))
           for im in ims]
    grid = Image.new("RGB", (sum(i.width for i in ims) + 10 * len(ims),
                             thumb_h + 40), "white")
    x = 0
    draw = ImageDraw.Draw(grid)
    for n, im in enumerate(ims, 1):
        grid.paste(im, (x, 40))
        draw.text((x + 10, 10), f"#{n}", fill="black")
        x += im.width + 10
    grid.save(out_png)
    return out_png


def last_frame(video, out_png):
    sh(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", str(video),
        "-frames:v", "1", "-update", "1", str(out_png)])
    return out_png


def assemble(clips, out_path, size, fps, drop_first_frame_of=()):
    """Join clips in order at one size/fps. Clips chained from a previous
    clip's last frame drop their duplicate first frame (no stutter)."""
    w, h = size
    inputs, parts = [], []
    for i, c in enumerate(clips):
        inputs += ["-i", str(c)]
        trim = "trim=start_frame=1," if i in drop_first_frame_of else ""
        parts.append(f"[{i}:v]{trim}setpts=PTS-STARTPTS,"
                     f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},"
                     f"setsar=1[v{i}]")
    has_audio = all(probe(c)["audio"] for c in clips)
    if has_audio:
        for i, c in enumerate(clips):
            off = 1 / fps if i in drop_first_frame_of else 0
            parts.append(f"[{i}:a]atrim=start={off},asetpts=PTS-STARTPTS,"
                         f"aresample=48000[a{i}]")
        chain = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
        parts.append(f"{chain}concat=n={len(clips)}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "320k"]
    else:
        chain = "".join(f"[v{i}]" for i in range(len(clips)))
        parts.append(f"{chain}concat=n={len(clips)}:v=1:a=0[v]")
        maps = ["-map", "[v]"]
    r = sh(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
            ";".join(parts), *maps, "-c:v", "libx264", "-preset", "medium",
            "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(out_path)])
    if r.returncode:
        raise RuntimeError(r.stderr[-1500:])
    return out_path
