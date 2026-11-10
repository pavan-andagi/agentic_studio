"""Agent 5 - Clips, finishing, assembly and editing suggestions."""
import json
import random
import shutil
from pathlib import Path

from ..approval import gate, open_file
from ..media import assemble, contact_sheet, last_frame, probe, validate
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


def _video(files):
    vids = [f for f in files if f.suffix.lower() in (".mp4", ".webm", ".mov")]
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
        while True:
            if s["start_from"] == "previous_last_frame" and idx > 0:
                start = last_frame(done[shots[idx - 1]["id"]],
                                   p.dir / "keyframes" / f"{sid}_from_prev.png")
            else:
                start = kf[f"{sid}_start"]
            values = {"@PROMPT": s["motion_prompt"],
                      "@IMAGE": ctx.comfy.upload(start),
                      "@SEED": random.randint(1, 2**31), "@WIDTH": w,
                      "@HEIGHT": h, "@DURATION": s["duration"],
                      "@FPS": d["fps"], "@FILENAME": f"studio/{p.dir.name}_{sid}"}
            wf = cfg["comfy"]["workflows"]["i2v"]
            if s["mode"] == "flf2v":
                values["@LAST_IMAGE"] = ctx.comfy.upload(kf[f"{sid}_end"])
                wf = cfg["comfy"]["workflows"]["flf2v"]
            print(f"\n[Clip agent] {sid} ({s['mode']}, {s['duration']}s) ...")
            vid = _video(ctx.comfy.run(wf, values, p.dir / "clips" / "takes",
                                       label=sid))
            info, issues = validate(vid)
            sheet = contact_sheet(vid, p.dir / "sheets" / f"{sid}.png")
            open_file(vid)
            action, val = gate(
                f"Clip {sid}", f"{s['beat']}\n{info['width']}x{info['height']}"
                f" {info['fps']}fps {info['duration']}s audio={info['audio']}"
                f"\nAuto-check: {'; '.join(issues) if issues else 'clean'}"
                f"\nVideo: {vid}\nFrames: {sheet}", vid,
                "[t] retake with a new seed   or a path to your own clip")
            if action == "approve" or (action == "pick" and Path(val).exists()):
                src = vid if action == "approve" else Path(val)
                dest = p.dir / "clips" / f"{sid}.mp4"
                shutil.copy(src, dest)
                (p.dir / "clips" / f"{sid}_4k.mp4").unlink(missing_ok=True)
                done[sid] = str(dest)
                p.save()
                break
            if action == "pick" and val.lower() == "t":
                continue
            if action == "revise":
                p.log("clips", "human", f"{sid}: {val}")
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
                files = ctx.comfy.run(cfg["comfy"]["workflows"]["upscale"], {
                    "@VIDEO": ctx.comfy.upload(clip),
                    "@FILENAME": f"studio/{p.dir.name}_{s['id']}_4k"},
                    p.dir / "clips" / "4k", label=f"{s['id']} 4K")
                shutil.copy(_video(files), out)
            clip = out
        finished.append(clip)

    # ---- assembly ----
    size = probe(finished[0])
    chained = [i for i, s in enumerate(shots)
               if s["start_from"] == "previous_last_frame" and i > 0]
    final = assemble(finished, p.dir / "final" / f"{p.dir.name}.mp4",
                     (size["width"], size["height"]), round(size["fps"]),
                     chained)
    info, issues = validate(final)

    # ---- editing suggestions ----
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
