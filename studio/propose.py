"""Hand an analysis to the showrunner, and build the project it allows.

The analyser reads a reference and writes a plan. That plan is a
document until somebody says to act on it - which is the point of this
step being separate. When they do, the Master agent reviews it the way
it reviews anything else: is the evidence enough, where does the work
actually start, and what is going to disappoint them.

    python -m studio.propose <project>
    python -m studio.propose <project> --use-frames

`<project>` is a directory holding analysis.json, as written by
`studio.analyse video`. On a "go" this writes 3_script.json, copies in
whatever keyframes the evidence supports, marks the stages the analysis
already covers, and stops. It does not start the run - the human does,
having read what the showrunner said about it.

On "not yet" it writes nothing but its reasons.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from .agents import master
from .approval import STAGES, Project
from .llm import LLM

# Everything before the stage we start at is, by definition, done.
# Marking them is what makes main.py's "start at the first unapproved
# stage" land in the right place - the same mechanism the entry modes
# use, driven by a decision instead of a flag.
def mark_up_to(p, stage):
    done = []
    for s in STAGES:
        if s == stage:
            break
        p.mark(s)
        done.append(s)
    return done


def normalise(plan, cfg):
    """The analyser's shots, in the shape 3_script.json expects."""
    seconds = int((cfg.get("defaults") or {}).get("max_clip_seconds", 5))
    shots = []
    for i, sh in enumerate(plan.get("shots") or [], 1):
        sid = sh.get("id") or f"s{i:02d}"
        shots.append({
            "id": sid,
            "beat": sh.get("note") or sh.get("motion_prompt", "")[:80],
            "duration": max(1, min(seconds, int(sh.get("seconds")
                                                or seconds))),
            "mode": sh.get("mode") if sh.get("mode") in ("i2v", "flf2v")
                    else "i2v",
            "start_from": sh.get("start_from") or "new_keyframe",
            # A shot that chains off the previous clip has no keyframe
            # to describe, and a bare look block in that field reads
            # like a prompt somebody forgot to finish.
            "start_image_prompt":
                join_look(sh.get("start_image_prompt"),
                          plan.get("look_block"))
                if (sh.get("start_image_prompt") or "").strip()
                or sh.get("start_from") != "previous_last_frame" else None,
            "end_image_prompt": None,
            "motion_prompt": sh.get("motion_prompt") or "",
            "bubble_text": None, "bubble_type": None,
            "narration": None,
            "characters": sh.get("characters") or [],
            "location": sh.get("location"),
            "props": [], "overlays": [], "safe_area": None,
            "edit_note": sh.get("camera") or "",
        })
    audio = plan.get("audio") or {}
    return {"shots": shots,
            "voiceover": audio.get("narration") or None,
            "captions": {"instagram": "", "youtube_title": "",
                         "youtube_description": "", "hashtags": []}}


def join_look(body, tail):
    body, tail = (body or "").strip(), (tail or "").strip()
    if not tail:
        return body or None
    if body and body[-1] not in ".!?":
        body += "."
    return f"{body} {tail}".strip() or None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("name", help="the project holding analysis.json")
    ap.add_argument("--use-frames", action="store_true",
                    help="supply the reference frames as keyframes "
                    "instead of generating them. Copies the reference "
                    "closely rather than reinterpreting it")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    p = Project(cfg["projects_dir"], args.name)
    src = p.dir / "analysis.json"
    if not src.exists():
        sys.exit(f"{src} does not exist - run studio.analyse first")
    plan = json.loads(src.read_text(encoding="utf-8"))
    if not (plan.get("shots") or []):
        sys.exit("that analysis has no shots in it")

    frames = [Path(x) for x in (plan.get("reference_frames") or [])]
    frames = [x for x in frames if x.exists()]

    llm = LLM(cfg["llm"]["model"], cfg["llm"]["max_tokens"],
              cfg["llm"].get("web_search_max_uses", 0))
    d = master.admit(llm, cfg, plan, frames, args.use_frames)
    p.write_json("master_admit.json", d)
    verdict = master.describe_admit(d)
    (p.dir / "master_admit.md").write_text(verdict, encoding="utf-8")
    print("\n" + verdict + "\n")

    if d.get("verdict") != "go":
        p.event("plan", "master_decision", "plan not admitted")
        p.save()
        print("  nothing was created. Fix what is listed above and run "
              "the analysis again.")
        return

    start = d.get("start_stage") or "cast"
    p.write_json("3_script.json", normalise(plan, cfg))

    # Reference frames become keyframes only if the showrunner said so.
    # Copying them makes a close reproduction; generating makes
    # something in the same spirit, and that is the usual want.
    kf = p.state.setdefault("keyframes", {})
    if d.get("use_reference_frames") and frames:
        dest = p.dir / "keyframes"
        dest.mkdir(exist_ok=True)
        for i, sh in enumerate(plan.get("shots") or []):
            if i >= len(frames):
                break
            sid = sh.get("id") or f"s{i + 1:02d}"
            out = dest / f"{sid}_start.png"
            shutil.copy(frames[i], out)
            kf[f"{sid}_start"] = str(out)

    done = mark_up_to(p, start)
    p.state["entry"] = {"mode": "analysis", "at": start,
                        "skipped": done,
                        "from_analysis": str(src)}
    p.state["admitted"] = {"start_stage": start,
                           "summary": d.get("summary", ""),
                           "use_reference_frames":
                               bool(d.get("use_reference_frames"))}
    p.save()
    p.event(start, "master_decision",
            f"admitted from an analysis; starting at {start}")

    print(f"  3_script.json written: {len(plan['shots'])} shot(s)")
    if kf:
        print(f"  {len(kf)} keyframe(s) supplied from the reference")
    print(f"  stages marked done: {', '.join(done) or 'none'}")
    print(f"\n  Ready, beginning at {start.upper()}. Press 'Start the "
          f"run' in the browser,\n  or here:  python -m studio.main "
          f"resume {args.name}")


if __name__ == "__main__":
    main()
