"""Agent 9 - Render: build the deterministic layers and composite them.

Everything that has to be *correct* is drawn here, in code, rather than
asked of a diffusion model: captions, titles, labels, vector graphics.
Generated text is the single most visible tell in an AI video, and it is
also the easiest thing in the pipeline to fix - you simply stop asking the
model for it.

Layers are drawn once as transparent PNGs and timed by ffmpeg, so a
caption costs one image and a filter expression rather than a frame
sequence. Only genuinely animated overlays need a sequence, and those are
not in this step.

This stage runs AFTER Finish, so the look is already on the plate and the
type stays crisp on top of it."""
import glob
import hashlib
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import compose, style
from .. import filters as F
from .base import StageResult
from .finish import _sources

# Where the type sits, as a fraction of frame height.
ANCHORS = {
    "top": 0.08, "upper-third": 0.24, "center": 0.5,
    "lower-third": 0.74, "bottom": 0.88,
}

# Preference order. The first one that exists wins; override with
# render.font in config.yaml.
FONT_HINTS = ("DejaVuSans-Bold", "LiberationSans-Bold", "FreeSansBold",
              "NotoSans-Bold", "Arial Bold", "DejaVuSans", "Arial")

DEFAULTS = {
    "caption": {"size": .042, "colour": [255, 255, 255], "width": .76,
                "stroke": 3, "shadow": 4, "align": "center"},
    "title":   {"size": .085, "colour": [255, 255, 255], "width": .82,
                "stroke": 4, "shadow": 6, "align": "center"},
    "label":   {"size": .030, "colour": [255, 255, 255], "width": .35,
                "stroke": 2, "shadow": 3, "align": "left"},
}


def _font_file(cfg):
    want = (cfg.get("render") or {}).get("font")
    if want and Path(want).exists():
        return want
    pool = []
    for root in ("/usr/share/fonts", "/usr/local/share/fonts",
                 str(Path.home() / ".fonts")):
        pool += glob.glob(root + "/**/*.ttf", recursive=True)
        pool += glob.glob(root + "/**/*.otf", recursive=True)
    for hint in FONT_HINTS:
        for f in pool:
            if hint.lower().replace(" ", "") in Path(f).stem.lower():
                return f
    return pool[0] if pool else None


def _wrap(draw, text, font, max_px):
    """Wrap on measured width, not on a character count."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_px or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _draw_text(size, spec, text, font_file):
    """One transparent PNG the size of the frame, with the type on it."""
    w, h = size
    kind = spec.get("type", "caption")
    d = dict(DEFAULTS.get(kind, DEFAULTS["caption"]))
    d.update({k: v for k, v in spec.items() if k in d})

    px = max(10, int(float(d["size"]) * h))
    font = (ImageFont.truetype(font_file, px) if font_file
            else ImageFont.load_default())
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)

    max_px = int(float(d["width"]) * w)
    lines = _wrap(dr, text, font, max_px)
    lh = int(px * 1.28)
    block = lh * len(lines)
    cy = ANCHORS.get(spec.get("anchor", "lower-third"), .74) * h
    y = int(cy - block / 2)

    side = max(int(.06 * w), 16)
    colour = tuple(int(c) for c in d["colour"]) + (255,)
    stroke = int(d["stroke"])
    shadow = int(d["shadow"])

    for ln in lines:
        tw = dr.textlength(ln, font=font)
        if d["align"] == "left":
            x = side
        elif d["align"] == "right":
            x = w - side - tw
        else:
            x = (w - tw) / 2
        if shadow:
            dr.text((x + shadow * .5, y + shadow * .6), ln, font=font,
                    fill=(0, 0, 0, 120))
        dr.text((x, y), ln, font=font, fill=colour,
                stroke_width=stroke, stroke_fill=(0, 0, 0, 190))
        y += lh
    return img


def _overlays_for(script, sid):
    for sh in (script or {}).get("shots", []):
        if sh.get("id") == sid:
            return sh.get("overlays") or [], float(sh.get("duration") or 0)
    return [], 0.0


def _mark(spec, plate):
    h = hashlib.sha1()
    h.update(json.dumps(spec, sort_keys=True, default=str).encode())
    try:
        s = Path(plate).stat()
        h.update(f"{s.st_size}:{int(s.st_mtime)}".encode())
    except OSError:
        pass
    return h.hexdigest()[:16]


def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    rcfg = cfg.get("render") or {}
    if not rcfg.get("enabled", True):
        return StageResult("Render stage is switched off in config.yaml.",
                           None, {})

    # Finished plates if Finish ran, raw clips otherwise.
    plates = {k: Path(v) for k, v in (p.state.get("finished") or {}).items()}
    if not plates:
        plates = _sources(p)
    if not plates:
        return StageResult("No clips to render overlays onto yet.", None, {})

    script = p.read_json("3_script.json")
    prof = style.load(cfg, p.state.get("style"))

    # The stage skips itself when it has nothing to do - it does not read
    # a flag on the profile. A style that says "no overlays" and a script
    # that emits captions anyway would lose them silently.
    work = {sid: _overlays_for(script, sid)[0] for sid in plates}
    if not any(work.values()):
        passthrough = {k: str(v) for k, v in plates.items()}
        final = compose.reassemble(ctx, passthrough)
        return StageResult(
            f"No overlays in the script: {len(plates)} clips pass through "
            f"unchanged." + (f"\nFinal: {final}" if final else ""),
            final, {"composed": passthrough,
                    **({"final": str(final)} if final else {})})

    if not F.have_ffmpeg():
        print("\n  WARNING: ffmpeg not found - skipping Render.\n")
        return StageResult("ffmpeg is missing, overlays not composited.",
                           None, {})

    font_file = _font_file(cfg)
    if not font_file:
        print("\n  WARNING: no TrueType font found. Set render.font in "
              "config.yaml to a .ttf path, or text will be tiny.\n")

    lay_dir = p.dir / "overlays"
    out_dir = p.dir / "composed"
    lay_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    done = p.state.setdefault("composed", {})
    marks = p.state.setdefault("composed_marks", {})
    if feedback:
        marks.clear()

    only = set(items or [])
    n_over = sum(len(v) for v in work.values())
    print(f"\n[Render agent] {n_over} overlays over {len(plates)} clips ...")
    p.set_status("render", "working", f"compositing {n_over} layers")

    changed = 0
    for sid, plate in sorted(plates.items()):
        if only and sid not in only:
            continue
        specs, dur = _overlays_for(script, sid)
        dst = out_dir / f"{sid}{plate.suffix.lower()}"
        mark = _mark(specs, plate)
        if marks.get(sid) == mark and dst.exists():
            done[sid] = str(dst)
            continue

        w, h = F.probe(plate)[:2]
        layers = []
        for i, spec in enumerate(specs):
            text = (spec.get("text") or "").strip()
            png = lay_dir / f"{sid}_{i}.png"
            if spec.get("type") == "svg" and spec.get("png"):
                png = Path(spec["png"])          # pre-rendered, use as is
            elif text:
                img = _draw_text((w, h), spec, text, font_file)
                img.save(png)
            else:
                continue
            layers.append({
                "png": str(png),
                "in": float(spec.get("in", 0)),
                "out": float(spec.get("out") or dur or 0),
                "fade": float(spec.get("fade", rcfg.get("fade", .25))),
            })

        n = compose.compose(plate, layers, dst,
                            crf=int(rcfg.get("crf", 17)),
                            fade=float(rcfg.get("fade", .25)))
        done[sid], marks[sid] = str(dst), mark
        changed += 1
        print(f"  {sid}: {n} layer(s) -> {dst.name}")
        p.save()

    p.event("render", "agent_done", f"{n_over} overlays on {changed} clips")

    # Last stage to touch the clips, so this is where the film is joined
    # back together.
    final = compose.reassemble(ctx, done)
    patch = {"composed": dict(done), "composed_marks": dict(marks)}
    if final:
        patch["final"] = str(final)

    lines = "\n".join(f"  {k}: {v}" for k, v in sorted(done.items()))
    return StageResult(
        f"Overlays composited ({prof.name} style, text drawn in code, not "
        f"generated).\n{changed} clip(s) rendered, "
        f"{len(done) - changed} already current."
        + (f"\nFinal: {final}" if final else "")
        + f"\n\n{lines}\n\n"
        f"Edit the overlay text in 3_script.json and re-run this stage "
        f"alone: the plates are untouched.",
        final, patch,
        [Path(v) for v in done.values() if Path(v).exists()][:6])
