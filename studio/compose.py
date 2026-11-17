"""Composite overlay layers onto a finished plate.

Overlays go on AFTER the filters, not before. A caption that has been
through a halftone screen or a pixelate is unreadable, which defeats the
reason for compositing it in the first place: generated text is the most
visible tell in an AI video, and crisp text is the fix.

Pass bake=True on an overlay when you do want it to sit under the look -
a title burned into a VHS plate, say."""
import subprocess
from pathlib import Path


def _esc(p):
    """ffmpeg filter arguments need their own escaping."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_graph(layers, fade=0.25):
    """filter_complex for N overlay inputs, each timed and faded.

    Input 0 is the plate; overlay i is input i+1."""
    parts, cur = [], "0:v"
    for i, lay in enumerate(layers):
        src, out = f"{i + 1}:v", f"v{i + 1}"
        t_in = float(lay.get("in", 0))
        t_out = float(lay.get("out", 0))
        f = float(lay.get("fade", fade))
        chain = [f"[{src}]format=rgba"]
        if f > 0 and t_out > t_in:
            f = min(f, (t_out - t_in) / 2)
            chain.append(f"fade=t=in:st={t_in:.3f}:d={f:.3f}:alpha=1")
            chain.append(f"fade=t=out:st={max(t_in, t_out - f):.3f}"
                         f":d={f:.3f}:alpha=1")
        pre = ",".join(chain) + f"[o{i}]"
        parts.append(pre)
        en = (f":enable='between(t,{t_in:.3f},{t_out:.3f})'"
              if t_out > t_in else "")
        parts.append(f"[{cur}][o{i}]overlay=0:0:format=auto{en}[{out}]")
        cur = out
    return ";".join(parts), cur


def compose(plate, layers, dst, crf=17, fade=0.25):
    """Burn `layers` (each {png, in, out}) onto `plate` and write `dst`."""
    plate, dst = Path(plate), Path(dst)
    layers = [l for l in layers if Path(l["png"]).exists()]
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not layers:                       # nothing to do - keep the plate
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(plate),
                        "-c", "copy", str(dst)], check=True)
        return 0

    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(plate)]
    for l in layers:
        cmd += ["-loop", "1", "-i", str(l["png"])]
    graph, last = build_graph(layers, fade)
    cmd += ["-filter_complex", graph, "-map", f"[{last}]"]
    cmd += ["-map", "0:a?", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-shortest", str(dst)]
    subprocess.run(cmd, check=True)
    return len(layers)


def reassemble(ctx, clips_map):
    """Rebuild final/<name>.mp4 from a per-shot map.

    The Clips agent assembles the film as its last act, so any stage that
    changes the individual clips afterwards has to join them again. Same
    arguments as the original assembly, so transitions and chaining behave
    identically."""
    from .media import assemble, probe

    p, cfg = ctx.project, ctx.cfg
    d = cfg["defaults"]
    script = p.read_json("3_script.json", {"shots": []}) or {"shots": []}
    shots = [s for s in script["shots"] if s["id"] in clips_map]
    files = [Path(clips_map[s["id"]]) for s in shots]
    files = [f for f in files if f.exists()]
    if not files or len(files) != len(shots):
        return None
    size = probe(files[0])
    chained = [i for i, s in enumerate(shots)
               if s.get("start_from") == "previous_last_frame" and i > 0]
    return assemble(
        files, p.dir / "final" / f"{p.dir.name}.mp4",
        (size["width"], size["height"]), round(size["fps"]), chained,
        keep_audio=(cfg.get("audio") or {}).get("keep_clip_audio", False),
        transition=d.get("transition", "none"),
        transition_seconds=d.get("transition_seconds", 0.5))
