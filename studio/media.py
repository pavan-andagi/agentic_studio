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


def assemble(clips, out_path, size, fps, drop_first_frame_of=(),
             keep_audio=True, transition="none", transition_seconds=0.5):
    """Join clips in order at one size/fps. Clips chained from a previous
    clip's last frame drop their duplicate first frame and join seamlessly.
    Hard cuts (a new keyframe) can get a soft transition:
    transition = none | dissolve | fadeblack (ffmpeg xfade), lasting
    transition_seconds. keep_audio=False drops the clips' own sound."""
    w, h = size
    xf = {"dissolve": "fade", "fadeblack": "fadeblack"}.get(transition)
    T = float(transition_seconds) if xf else 0.0
    inputs, parts, durs = [], [], []
    for i, c in enumerate(clips):
        inputs += ["-i", str(c)]
        trim = "trim=start_frame=1," if i in drop_first_frame_of else ""
        parts.append(f"[{i}:v]{trim}setpts=PTS-STARTPTS,"
                     f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},"
                     f"setsar=1,format=yuv420p,settb=AVTB[v{i}]")
        d = probe(c)["duration"] - (1 / fps if i in drop_first_frame_of else 0)
        durs.append(d)
    has_audio = keep_audio and all(probe(c)["audio"] for c in clips)
    if has_audio:
        for i, c in enumerate(clips):
            off = 1 / fps if i in drop_first_frame_of else 0
            parts.append(f"[{i}:a]atrim=start={off},asetpts=PTS-STARTPTS,"
                         f"aresample=48000[a{i}]")
    # join left to right: concat at chained joins, xfade at hard cuts
    cur_v, cur_a, cur_d = "v0", "a0", durs[0]
    for i in range(1, len(clips)):
        soft = T > 0 and i not in drop_first_frame_of and \
            min(cur_d, durs[i]) > 2 * T
        nv, na = f"jv{i}", f"ja{i}"
        if soft:
            parts.append(f"[{cur_v}][v{i}]xfade=transition={xf}:duration={T}:"
                         f"offset={cur_d - T:.3f}[{nv}]")
            if has_audio:
                parts.append(f"[{cur_a}][a{i}]acrossfade=d={T}[{na}]")
            cur_d += durs[i] - T
        else:
            parts.append(f"[{cur_v}][v{i}]concat=n=2:v=1:a=0[{nv}]")
            if has_audio:
                parts.append(f"[{cur_a}][a{i}]concat=n=2:v=0:a=1[{na}]")
            cur_d += durs[i]
        cur_v, cur_a = nv, na
    if len(clips) == 1:
        parts.append("[v0]null[vout]")
        if has_audio:
            parts.append("[a0]anull[aout]")
    else:
        parts.append(f"[{cur_v}]null[vout]")
        if has_audio:
            parts.append(f"[{cur_a}]anull[aout]")
    maps = ["-map", "[vout]"]
    if has_audio:
        maps += ["-map", "[aout]", "-c:a", "aac", "-b:a", "320k"]
    r = sh(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
            ";".join(parts), *maps, "-c:v", "libx264", "-preset", "medium",
            "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(out_path)])
    if r.returncode:
        raise RuntimeError(r.stderr[-1500:])
    return out_path


# ---------------------------------------------------------------- audio
AUDIO_EXT = (".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac")
STEREO = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


def duration(path):
    """Length in seconds of any audio or video file."""
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)]).stdout.strip()
    return round(float(out), 3) if out else 0.0


def _run(cmd, what):
    r = sh(cmd)
    if r.returncode:
        raise RuntimeError(f"ffmpeg {what} failed: {r.stderr[-1500:]}")


def voice_track(segments, total, out_wav):
    """Place voice lines on a silent timeline: segments = [(file, start_s)]."""
    inputs, parts = [], []
    for i, (f, t) in enumerate(segments):
        inputs += ["-i", str(f)]
        ms = max(0, int(round(t * 1000)))
        parts.append(f"[{i}:a]{STEREO},adelay={ms}|{ms}[s{i}]")
    n = len(segments)
    parts.append("".join(f"[s{i}]" for i in range(n))
                 + f"amix=inputs={n}:normalize=0:dropout_transition=0,"
                 f"apad,atrim=0:{total:.3f}[out]")
    _run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
          ";".join(parts), "-map", "[out]", "-c:a", "pcm_s16le",
          str(out_wav)], "voice track")
    return out_wav


def fit_length(src, total, out_wav, xfade=2.0):
    """Trim a track to `total` s, or loop it with crossfades if too short."""
    d = duration(src)
    if d >= total or d <= 2 * xfade + 0.5:
        loop = [] if d >= total else ["-stream_loop", "-1"]
        _run(["ffmpeg", "-v", "error", "-y", *loop, "-i", str(src), "-af",
              f"{STEREO},atrim=0:{total:.3f}", "-c:a", "pcm_s16le",
              str(out_wav)], "fit length")
        return out_wav
    n = 2
    while d * n - xfade * (n - 1) < total:
        n += 1
    inputs, parts, last = [], [], "0:a"
    for i in range(n):
        inputs += ["-i", str(src)]
    for i in range(1, n):
        parts.append(f"[{last}][{i}:a]acrossfade=d={xfade}[x{i}]")
        last = f"x{i}"
    parts.append(f"[{last}]{STEREO},atrim=0:{total:.3f}[out]")
    _run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
          ";".join(parts), "-map", "[out]", "-c:a", "pcm_s16le",
          str(out_wav)], "loop")
    return out_wav


def mix(video, out_path, voice=None, music=None, ambience=None,
        clip_audio=False, levels=None, duck=True, lufs=-14):
    """Put the audio layers under the (silent) picture.
    levels: dB per layer, e.g. {"voice": 0, "music": -16, "ambience": -20,
    "clip": -12}. The music ducks under the voice; the result is
    loudness-normalised (-14 LUFS suits YouTube and Instagram)."""
    lv = {"voice": 0, "music": -16, "ambience": -20, "clip": -12,
          **(levels or {})}
    total = duration(video)
    inputs, parts, layers = ["-i", str(video)], [], []
    fade = (f"afade=t=in:d=1,afade=t=out:st={max(0, total - 2):.3f}:d=2,"
            f"apad,atrim=0:{total:.3f}")
    k = 1
    if voice:
        inputs += ["-i", str(voice)]
        vo = f"{k}:a"
        k += 1
        if music and duck:
            parts.append(f"[{vo}]{STEREO},volume={lv['voice']}dB,"
                         f"apad,atrim=0:{total:.3f},asplit=2[vo][vsc]")
        else:
            parts.append(f"[{vo}]{STEREO},volume={lv['voice']}dB,"
                         f"apad,atrim=0:{total:.3f}[vo]")
        layers.append("[vo]")
    if music:
        inputs += ["-i", str(music)]
        mu = f"{k}:a"
        k += 1
        parts.append(f"[{mu}]{STEREO},atrim=0:{total:.3f},"
                     f"volume={lv['music']}dB,{fade}[mu0]")
        if voice and duck:
            parts.append("[mu0][vsc]sidechaincompress=threshold=0.03:"
                         "ratio=6:attack=30:release=500[mu]")
        else:
            parts.append("[mu0]anull[mu]")
        layers.append("[mu]")
    if ambience:
        inputs += ["-i", str(ambience)]
        am = f"{k}:a"
        k += 1
        parts.append(f"[{am}]{STEREO},atrim=0:{total:.3f},"
                     f"volume={lv['ambience']}dB,{fade}[am]")
        layers.append("[am]")
    if clip_audio and probe(video)["audio"]:
        parts.append(f"[0:a]{STEREO},volume={lv['clip']}dB,"
                     f"apad,atrim=0:{total:.3f}[ca]")
        layers.append("[ca]")
    if not layers:
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-c:v", "copy",
              "-an", str(out_path)], "copy")
        return out_path
    parts.append("".join(layers) + f"amix=inputs={len(layers)}:normalize=0:"
                 f"dropout_transition=0,atrim=0:{total:.3f}[aout]")
    raw = Path(out_path).with_suffix(".premix.wav")
    _run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
          ";".join(parts), "-map", "[aout]", "-c:a", "pcm_s24le", str(raw)],
         "mix")
    norm = normalize(raw, lufs)
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(raw),
          "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", norm,
          "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
          "-t", f"{total:.3f}", str(out_path)], "mux")
    raw.unlink(missing_ok=True)
    return out_path


def normalize(audio, lufs=-14, tp=-1.5):
    """Two-pass loudnorm: measure, then return the exact filter to apply."""
    r = sh(["ffmpeg", "-hide_banner", "-i", str(audio), "-af",
            f"loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json",
            "-f", "null", "-"])
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    if float(m["input_i"]) < -70:          # silence: nothing to normalise
        return STEREO
    return (f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={m['input_i']}:"
            f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
            f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}"
            f":linear=true,{STEREO}")
