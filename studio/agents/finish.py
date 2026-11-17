"""Agent 8 - Finish: impose the final look on the generated clips.

Nothing here touches a model. Every clip is run through a chain of pure
functions, so the whole film can be re-graded in seconds. That is the
point: a look you can iterate on for free is a look you will actually
iterate on.

It is also how several styles become reachable at all. Pixel art, Benday
halftone, risograph and VHS are not things to ask a diffusion model for -
it works in continuous space and will smear every one of them. Generate a
clean plate in a style the model is good at, then impose the look here,
where it cannot drift between frames.

Order is fixed and matters: filters, then interpolation, then upscale.
Interpolating after a dot screen produces mush; upscaling before a
pixelate undoes the pixelate."""
import hashlib
import json
from pathlib import Path

from .. import compose
from .. import filters as F
from ..approval import STAGES
from .base import StageResult

VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv")


def _sources(p):
    """{shot_id: Path} for the approved clips.

    state["clips"] is {shot_id: path}, written by the Clips agent. Where
    4K finishing ran, the assembly uses <id>_4k.mp4 instead, so prefer
    that file: the graded clips must match what gets joined."""
    out = {}
    for sid, path in (p.state.get("clips") or {}).items():
        if not path:
            continue
        base = Path(path)
        up = base.with_name(f"{sid}_4k.mp4")
        out[sid] = up if up.exists() else base
    return out


def _fingerprint(src, spec):
    """Changing the filter spec must re-render. Changing nothing must not.

    This is the whole reason finishing is cheap: the plate above is
    untouched, so only this stage re-runs."""
    h = hashlib.sha1()
    h.update(json.dumps(spec, sort_keys=True, default=str).encode())
    try:
        s = src.stat()
        h.update(f"{s.st_size}:{int(s.st_mtime)}".encode())
    except OSError:
        pass
    return h.hexdigest()[:16]


def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    fcfg = cfg.get("finish") or {}

    if not fcfg.get("enabled", True):
        return StageResult("Finish stage is switched off in config.yaml.",
                           None, {})

    # The style profile owns the look; config.finish.filters is an override
    # for one-off experiments.
    style = (cfg.get("styles") or {}).get(
        p.state.get("style") or (cfg.get("styles") or {}).get("default"))
    spec = fcfg.get("filters")
    if spec is None and isinstance(style, dict):
        spec = style.get("filters")
    spec = spec or []

    srcs = _sources(p)
    if not srcs:
        return StageResult("No clips to finish yet.", None, {})
    if not spec:
        return StageResult(
            f"No filters for this style: {len(srcs)} clips pass through "
            f"unchanged.", None, {"finished": {k: str(v)
                                               for k, v in srcs.items()}})
    if not F.have_ffmpeg():
        print("\n  WARNING: ffmpeg/ffprobe not found - skipping Finish.\n")
        return StageResult("ffmpeg is missing, clips left unfiltered.",
                           None, {})

    names = ", ".join(f.get("name", "?") for f in spec)
    out_dir = p.dir / "finished"
    out_dir.mkdir(exist_ok=True)
    done = p.state.setdefault("finished", {})
    marks = p.state.setdefault("finished_marks", {})

    # A feedback pass re-renders everything: the whole point is that it
    # is cheap, so there is no reason to be clever about scope.
    only = set(items or [])
    if feedback:
        marks.clear()

    print(f"\n[Finish agent] {names} over {len(srcs)} clips ...")
    p.set_status("finish", "working", f"applying {names}")

    changed = 0
    for sid, src in sorted(srcs.items()):
        if only and sid not in only:
            continue
        if not src.exists():
            print(f"  {sid}: source missing, skipped")
            continue
        mark = _fingerprint(src, spec)
        dst = out_dir / f"{sid}{src.suffix.lower()}"
        if marks.get(sid) == mark and dst.exists():
            done[sid] = str(dst)
            continue
        chain = F.build(spec)
        n = F.apply_chain(src, dst, chain, crf=int(fcfg.get("crf", 17)))
        done[sid], marks[sid] = str(dst), mark
        changed += 1
        print(f"  {sid}: {n} frames -> {dst.name}")
        p.save()

    p.event("finish", "agent_done", f"{names} on {changed} clips")

    # Clips assembles the film as its last act, so the graded clips have
    # to be joined again. Render runs after this and joins them itself, so
    # only do it here when Render is not in the pipeline or is switched off.
    patch = {"finished": dict(done), "finished_marks": dict(marks)}
    final = None
    render_next = ("render" in STAGES
                   and (cfg.get("render") or {}).get("enabled", True))
    if not render_next:
        final = compose.reassemble(ctx, done)
        if final:
            patch["final"] = str(final)

    lines = "\n".join(f"  {k}: {v}" for k, v in sorted(done.items()))
    return StageResult(
        f"Look applied: {names}\n"
        f"{changed} clip(s) rendered, {len(done) - changed} already current."
        f"{chr(10) + 'Final: ' + str(final) if final else ''}"
        f"\n\n{lines}\n\nChange the filters in config.yaml and re-run this "
        f"stage alone: the plates above are untouched.",
        final or next((Path(v) for v in done.values() if Path(v).exists()),
                      None),
        patch,
        [Path(v) for v in done.values() if Path(v).exists()][:6])
