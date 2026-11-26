"""Agent 5 - Clips, finishing, assembly and editing suggestions."""
import json
import random
import shutil
from pathlib import Path

from ..approval import gate, open_file
from ..media import assemble, contact_sheet, last_frame, probe, sh, validate
from .base import StageResult, history, rules

REWRITE = """You rewrite one LTX-2.5 motion prompt using the feedback and
the observed problems. Keep what worked.
Return JSON: {"motion_prompt": str, "duration": int}"""

EDITOR = """You are a video editor for short-form social content. Given the
script and one contact sheet per clip (in order), suggest concrete edits:
trims (with seconds), speed changes, bubble/title placement and timing,
sound effects and music cues, color grade, cover frame, and anything to
regenerate. Be specific and brief.
Return JSON: {"edits": [{"shot": str, "suggestion": str}], "music": str,
 "grade": str, "cover_frame": str, "regenerate": [str],
 "captions": {"instagram": str, "youtube_title": str,
              "youtube_description": str}}"""


def animate_of(s):
    """A shot that is a character swap in a supplied clip, or None.

    {"video": path, "reference": path, "instruction": str, "point": [x, y]}

    This is not a keyframe being animated - it is somebody else's clip
    with one character replaced, motion and all. Wan 2.2 Animate takes
    the video itself, so there is no keyframe to make and nothing for
    the Images stage to do."""
    a = s.get("animate")
    if isinstance(a, dict) and a.get("video") and a.get("reference"):
        return a
    return None


def _wan_frames(seconds, fps):
    """Wan's latent packing wants 4n+1 frames, so round to one.

    Asking for 48 silently becomes something else inside the node; being
    explicit here means the clip is the length the script said."""
    n = max(5, int(round(float(seconds) * float(fps))))
    return n - ((n - 1) % 4)


MAX_DRIVE_FPS = 30


def _drive(an, seconds, fallback_fps, work):
    """The driving clip, the rate to write it at, and how many frames.

    Wan consumes the clip frame by frame and writes the result at
    whatever rate it is told, so the two have to agree. A 24 fps clip
    rendered at 16 plays half again as slow, and runs out of story
    two-thirds of the way through: 141 frames is 5.9 seconds of a 24 fps
    source stretched over 8.8 seconds of output.

    A swap exists to keep the original motion, so the clip's own rate
    wins. Only a rate too expensive to generate is brought down, and
    then the clip itself is resampled, so the motion still runs in real
    time rather than in slow motion."""
    src = Path(an["video"])
    try:
        info = probe(src)
    except Exception:                                        # noqa: BLE001
        fps = int(fallback_fps)
        return src, fps, _wan_frames(seconds, fps)

    fps = int(round(info["fps"])) or int(fallback_fps)
    use = src
    if fps > MAX_DRIVE_FPS:
        fps = MAX_DRIVE_FPS
        use = Path(work) / f"{src.stem}_{fps}fps.mp4"
        if not use.exists():
            sh(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                "-vf", f"fps={fps}", "-an", str(use)])
    # Asking for more seconds than the clip holds gives Wan nothing to
    # follow past the end, and it invents motion there.
    secs = min(float(seconds), float(info["duration"]))
    return use, fps, _wan_frames(secs, fps)


def _point(a, w, h):
    """Where SAM 2 should look for the character being replaced.

    The centre of the frame is right surprisingly often - a subject
    worth replacing is usually the thing the camera is pointed at - and
    it means a swap can run unattended. An explicit point overrides it,
    as a fraction of the frame (0.5, 0.4) or as pixels (320, 512).

    Several points are allowed, and are worth it on an animal or a
    seated figure: one point returns whichever part SAM 2 thinks you
    meant, which is how a swap comes back with a new head on the old
    body. A point on the head and another on the body asks for both."""
    return _points(a.get("point"), w, h) or [{"x": w // 2, "y": h // 2}]


def _points(seq, w, h):
    """[x, y, x, y, ...] or [[x, y], ...] -> SAM 2 coordinates.

    Fractions of the frame and pixels both work, and anything that is
    not a number is dropped rather than raising: a coordinate typed at
    a gate should never end a run."""
    flat = []
    for v in seq or []:
        if isinstance(v, (list, tuple)) and len(v) == 2:
            flat += [v[0], v[1]]
        else:
            flat.append(v)
    out = []
    for x, y in zip(flat[0::2], flat[1::2]):
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            continue
        if 0 < x <= 1 and 0 < y <= 1:          # a fraction of the frame
            x, y = x * w, y * h
        out.append({"x": int(round(x)), "y": int(round(y))})
    return out


def _repoint(a, text):
    """"point 0.3 0.6" -> ("point", [0.3, 0.6]), "avoid 0.1 0.2" ->
    ("avoid", ...), else None.

    Anything else is ordinary feedback and goes to the rewriter."""
    if not a:
        return None
    words = str(text).replace(",", " ").split()
    if not words:
        return None
    head = words[0].lower()
    if head in ("avoid", "not", "exclude"):
        kind = "avoid"
    elif head in ("point", "points", "pt"):
        kind = "point"
    else:
        return None
    nums = []
    for word in words[1:]:
        try:
            nums.append(float(word))
        except ValueError:
            continue
    # Pairs, so "point 0.5 0.3 0.45 0.62" marks a head and a body.
    if len(nums) < 2:
        return None
    return kind, nums[:len(nums) - len(nums) % 2]


def _video(files, prefer=None, drop=None):
    """The video the workflow actually made.

    A workflow that LOADS a video reports that file as an output too, so
    what comes back can contain the source clip as well as the result -
    and picking the first one hands back the input untouched. The name
    we asked SaveVideo for is the thing to look for; `drop` is the input
    filename, which should not be sitting in takes/ pretending to be a
    take."""
    vids = [f for f in files if f.suffix.lower() in (".mp4", ".webm", ".mov")]
    if drop:
        echoes = [f for f in vids if f.name == Path(drop).name]
        for f in echoes:
            f.unlink(missing_ok=True)
        vids = [f for f in vids if f not in echoes]
    if prefer:
        want = [f for f in vids if f.name.startswith(prefer)]
        if want:
            return want[0]
    if not vids:
        raise RuntimeError("Workflow produced no video file")
    return vids[0]


def _rewrite(ctx, s, feedback, issues=None):
    new = ctx.llm.ask_json(REWRITE + rules(ctx.cfg) + "\n" + history(ctx),
                           f"Prompt:\n{s['motion_prompt']}\nDuration: "
                           f"{s['duration']}\nAuto-check: {issues}\n"
                           f"Feedback: {feedback}")
    s["motion_prompt"] = new["motion_prompt"]
    s["duration"] = new.get("duration", s["duration"])


def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    d = cfg["defaults"]
    w, h = d["video_size"]
    script = p.read_json("3_script.json")
    shots, done, kf = script["shots"], p.state["clips"], p.state["keyframes"]

    for idx, s in enumerate(shots):
        sid = s["id"]
        if sid in done:
            continue
        if feedback and (not items or sid in items):
            _rewrite(ctx, s, feedback)
            p.write_json("3_script.json", script)
        an = animate_of(s)
        while True:
            if an:
                # A swap replaces a character in a clip that already
                # exists, so there is no keyframe and no @DURATION -
                # Wan counts frames, not seconds.
                wfk = "animate"
                if not (cfg["comfy"]["workflows"] or {}).get(wfk):
                    raise RuntimeError(
                        "this shot is a character swap, but config.yaml has "
                        "no comfy.workflows.animate - add "
                        "workflows/wan_animate.json")
                # The clip's own frame rate, not the studio's: see
                # _drive. Rendering a 24 fps source at 16 is what makes
                # a swap play in slow motion and stop early.
                drive, fps, frames = _drive(an, s["duration"],
                                            d["fps"], p.dir)
                values = {
                    "@VIDEO": ctx.comfy.upload(drive),
                    "@IMAGE": ctx.comfy.upload(an["reference"]),
                    "@PROMPT": (s["motion_prompt"]
                                or an.get("instruction") or ""),
                    "@POINTS": json.dumps(_point(an, w, h)),
                    "@WIDTH": w, "@HEIGHT": h,
                    "@FRAMES": frames,
                    "@FPS": fps, "@SEED": random.randint(1, 2**31),
                    "@FILENAME": f"studio/{p.dir.name}_{sid}"}
                avoid = _points(an.get("avoid"), w, h)
                if avoid:
                    values["@NEGPOINTS"] = json.dumps(avoid)
                wf = cfg["comfy"]["workflows"][wfk]
                what = f"swap, {frames / fps:.1f}s at {fps}fps"
            else:
                if s["start_from"] == "previous_last_frame" and idx > 0:
                    start = last_frame(
                        done[shots[idx - 1]["id"]],
                        p.dir / "keyframes" / f"{sid}_from_prev.png")
                else:
                    start = kf[f"{sid}_start"]
                values = {"@PROMPT": s["motion_prompt"],
                          "@IMAGE": ctx.comfy.upload(start),
                          "@SEED": random.randint(1, 2**31), "@WIDTH": w,
                          "@HEIGHT": h, "@DURATION": s["duration"],
                          "@FPS": d["fps"],
                          "@FILENAME": f"studio/{p.dir.name}_{sid}"}
                wf = cfg["comfy"]["workflows"]["i2v"]
                if s["mode"] == "flf2v":
                    values["@LAST_IMAGE"] = ctx.comfy.upload(kf[f"{sid}_end"])
                    wf = cfg["comfy"]["workflows"]["flf2v"]
                what = f"{s['mode']}, {s['duration']}s"
            print(f"\n[Clip agent] {sid} ({what}) ...")
            p.set_status("clips", "working",
                         f"generating clip {sid} ({what})")
            vid = _video(ctx.comfy.run(wf, values, p.dir / "clips" / "takes",
                                       label=sid),
                         prefer=f"{p.dir.name}_{sid}",
                         drop=an["video"] if an else None)
            info, issues = validate(vid)
            sheet = contact_sheet(vid, p.dir / "sheets" / f"{sid}.png")
            open_file(vid)
            where = ""
            if an:
                pt = _point(an, w, h)[0]
                where = (f"\nReplaced: the subject at ({pt['x']}, {pt['y']})"
                         f" - if that found the wrong one, say so and give "
                         f"a point, e.g. 'point 0.3 0.6'")
            action, val = gate(
                f"Clip {sid}", f"{s['beat']}\n{info['width']}x{info['height']}"
                f" {info['fps']}fps {info['duration']}s audio={info['audio']}"
                f"\nAuto-check: {'; '.join(issues) if issues else 'clean'}"
                f"{where}"
                f"\nVideo: {vid}\nFrames: {sheet}", vid,
                "[t] retake with a new seed   or a path to your own clip")
            if action == "approve" or (action == "pick" and Path(val).exists()):
                src = vid if action == "approve" else Path(val)
                dest = p.dir / "clips" / f"{sid}.mp4"
                shutil.copy(src, dest)
                (p.dir / "clips" / f"{sid}_4k.mp4").unlink(missing_ok=True)
                done[sid] = str(dest)
                p.save()
                p.event("clips", "human_pick", f"{sid}: clip approved")
                break
            if action == "pick" and val.lower() == "t":
                p.event("clips", "human_pick", f"{sid}: retake with a new seed")
                continue
            if action == "revise":
                p.log("clips", "human", f"{sid}: {val}")
                p.event("clips", "agent_feedback", f"{sid}: {val}")
                # On a swap, the usual complaint is that it replaced the
                # wrong subject, and no amount of prompt rewriting fixes
                # that - it is a coordinate. "point 0.3 0.6" says where.
                moved = _repoint(an, val) if an else None
                if moved:
                    kind, pts = moved
                    s["animate"]["avoid" if kind == "avoid"
                                 else "point"] = pts
                    p.write_json("3_script.json", script)
                    said = ("avoiding" if kind == "avoid"
                            else "looking at")
                    p.event("clips", "human_pick",
                            f"{sid}: {said} {pts}")
                    continue
                _rewrite(ctx, s, val, issues)
                p.write_json("3_script.json", script)
                continue
            if action == "quit":
                raise SystemExit("Saved. Resume later.")

    # ---- finishing (4K/60fps via your ComfyUI workflow) ----
    finished = []
    for s in shots:
        clip = Path(done[s["id"]])
        out = p.dir / "clips" / f"{s['id']}_4k.mp4"
        if d.get("upscale_4k"):
            if not out.exists():
                print(f"[Clip agent] finishing {s['id']} (4K) ...")
                p.set_status("clips", "working", f"finishing {s['id']} to 4K")
                files = ctx.comfy.run(cfg["comfy"]["workflows"]["upscale"], {
                    "@VIDEO": ctx.comfy.upload(clip),
                    "@FILENAME": f"studio/{p.dir.name}_{s['id']}_4k"},
                    p.dir / "clips" / "4k", label=f"{s['id']} 4K")
                shutil.copy(_video(files, prefer=f"{p.dir.name}_{s['id']}_4k",
                                   drop=clip), out)
            clip = out
        finished.append(clip)

    # ---- assembly ----
    p.set_status("clips", "working", "joining the clips into the final video")
    size = probe(finished[0])
    chained = [i for i, s in enumerate(shots)
               if s["start_from"] == "previous_last_frame" and i > 0]
    final = assemble(finished, p.dir / "final" / f"{p.dir.name}.mp4",
                     (size["width"], size["height"]), round(size["fps"]),
                     chained, keep_audio=(cfg.get("audio") or {}).get(
                         "keep_clip_audio", False),
                     transition=d.get("transition", "none"),
                     transition_seconds=d.get("transition_seconds", 0.5))
    info, issues = validate(final)

    # ---- editing suggestions ----
    p.set_status("clips", "working", "writing editing suggestions")
    sheets = [contact_sheet(c, p.dir / "sheets" / f"final_{i:02d}.png", 6)
              for i, c in enumerate(finished)]
    notes = ctx.llm.ask_json(EDITOR, f"Script:\n{json.dumps(script)}",
                             images=sheets)
    p.write_json("5_edit_notes.json", notes)
    open_file(final)
    edits = "\n".join(f"  {e['shot']}: {e['suggestion']}"
                      for e in notes["edits"])
    summary = (f"Final: {final}\n{info['width']}x{info['height']} "
               f"{info['fps']}fps {info['duration']}s | auto-check: "
               f"{'; '.join(issues) if issues else 'clean'}\n\nSuggested "
               f"edits:\n{edits}\nMusic: {notes['music']}\nGrade: "
               f"{notes['grade']}\nCover: {notes['cover_frame']}")
    data = {"final": str(final), "final_check": issues,
            "shots": [{"id": s["id"], "beat": s["beat"],
                       "duration": s["duration"]} for s in shots],
            "edit_notes": notes}
    return StageResult(summary, final, data, sheets)
