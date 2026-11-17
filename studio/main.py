"""Orchestrator: human reviews every stage; the Master agent turns the
human's feedback into a decision (go on, or go back to the root cause).

  python -m studio.main new  my_project --brief "..."
  python -m studio.main resume my_project
"""
import argparse
import os
import sys
import time
from pathlib import Path

import yaml

from . import dashboard
from .agents import (audio, cast, clips, finish, images, master, planner,
                     script, trend)
from .agents.base import Ctx
from .approval import STAGES, Project, ask, banner, bind, enable_web, review_gate
from .comfy import Comfy
from .llm import LLM

AGENTS = {"trend": trend, "plan": planner, "script": script,
          "cast": cast, "images": images, "clips": clips,
          "finish": finish, "audio": audio}
TITLES = {"trend": "1 Trend Identifier", "plan": "2 Project Planner",
          "script": "3 Script / Plot", "cast": "4 Cast",
          "images": "5 Image Creation", "clips": "6 Clips & Editing",
          "finish": "7 Finish (look)", "audio": "8 Audio"}

# Deterministic stages. They cost no GPU and re-run in seconds, so they
# show their result and move on instead of stopping to ask. A Master
# rewind still lands here with feedback, and then they do ask.
AUTO = {"finish"}


READS = {"trend": "channel brief", "plan": "1_trend_chosen.json",
         "script": "2_plan.json", "cast": "2_plan.json (characters)",
         "images": "3_script.json (image prompts) + cast/",
         "clips": "3_script.json + keyframes",
         "finish": "clips/ + the style filters in config.yaml",
         "audio": "3_script.json (narration) + final/ video"}


def stage_name(i):
    return STAGES[i]


def run_stage(ctx, stage):
    """Run one stage: agent -> human review -> (if feedback) Master decides.
    Returns None to move on, or (target, items, instructions) to go back."""
    p = ctx.project
    pending = p.take_pending(stage) or {}
    feedback, items = pending.get("feedback"), pending.get("items")
    while True:
        p.set_status(stage, "working", "agent is working")
        p.event(stage, "start", "Agent started"
                + (f" (feedback: {feedback[:120]})"
                   if feedback and feedback != planner.RELOAD else ""),
                reads=READS[stage])
        result = AGENTS[stage].run(ctx, feedback, items)
        p.event(stage, "output", result.summary[:400])
        if stage in AUTO and not feedback:
            # Nothing to decide: the result is on the dashboard, and
            # changing it is a config edit and a re-run, not an answer.
            p.set_status(stage, "done", "deterministic - nothing to decide")
            p.event(stage, "approved", "Auto-approved (deterministic stage)")
            p.mark(stage)
            return None
        p.set_status(stage, "waiting", "waiting for your review")
        action, text = review_gate(TITLES[stage], result.summary,
                                   result.artifact)
        if action == "quit":
            raise SystemExit("Saved. Resume later.")
        if action == "edited" and stage in ("plan", "script"):
            feedback, items = planner.RELOAD, None
            continue
        if action == "approve":
            p.log(stage, "human", "Approved.")
            p.event(stage, "approved", "You approved this stage")
            p.mark(stage)
            return None

        # ---- human gave feedback: the Master decides what happens ----
        p.log(stage, "human", text)
        p.event(stage, "human_feedback", text)
        if not ctx.cfg.get("master", {}).get("enabled", True):
            feedback, items = text, None          # no Master: redo stage
            if stage in ("images", "clips"):
                p.rewind(stage, None)
            continue
        p.set_status(stage, "master", "Master agent is analysing your feedback")
        d = master.decide(ctx, stage, result, text)
        banner("MASTER AGENT DECISION")
        print(master.describe(d))
        p.event(stage, "master_decision",
                (f"Go back to {d['target_stage'].upper()}" if d["verdict"] == "revisit"
                 else "Proceed") + f": {d.get('root_cause') or d.get('understanding', '')}",
                verdict=d["verdict"], target=d.get("target_stage"),
                items=d.get("affected_items") or [])
        if ctx.cfg.get("master", {}).get("confirm_decisions", False):
            if ask("Confirm the Master's decision", master.describe(d), None,
                   "Enter = carry it out; x = redo this stage with your "
                   "feedback instead", "review",
                   "\nPress Enter to carry this out, or type 'x' to redo "
                   "this stage with your feedback instead > ").lower() == "x":
                d = {"verdict": "revisit", "target_stage": stage,
                     "affected_items": [], "instructions": text,
                     "root_cause": "human override", "understanding": text}
        p.log(stage, "master", master.describe(d).replace("\n", " | "))

        if d["verdict"] == "proceed":
            nxt = STAGES[STAGES.index(stage) + 1:][:1]
            if nxt and d.get("notes_for_next_stage"):
                p.state["guidance"][nxt[0]] = d["notes_for_next_stage"]
            elif not nxt and d.get("notes_for_next_stage"):   # final stage
                notes = p.read_json("5_edit_notes.json", {})
                notes.setdefault("your_notes", []).append(
                    d["notes_for_next_stage"])
                p.write_json("5_edit_notes.json", notes)
            p.mark(stage)
            return None
        target, its = d["target_stage"], d.get("affected_items") or []
        if target == stage:                       # fix it right here
            if stage in ("images", "clips", "script", "finish"):
                p.rewind(stage, its)
            feedback, items = d["instructions"], its
            continue
        return target, its, d["instructions"]


def needs_comfy(cfg, stage):
    if stage in ("cast", "images", "clips"):
        return True
    a = cfg.get("audio") or {}
    return stage == "audio" and a.get("enabled", True) and any(
        (a.get(k) or {}).get("enabled", True) for k in ("music", "ambience"))


class Tee:
    """Copy everything printed to a project log the dashboard can show."""

    def __init__(self, stream, path):
        self.stream, self.f = stream, open(path, "a", encoding="utf-8")

    def write(self, text):
        self.stream.write(text)
        self.f.write(text)
        self.f.flush()
        return len(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main():
    ap = argparse.ArgumentParser(description="Agentic AI video studio")
    ap.add_argument("command", choices=["new", "resume"])
    ap.add_argument("name")
    ap.add_argument("--brief")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    p = Project(cfg["projects_dir"], args.name)
    if args.command == "new":
        if not args.brief:
            sys.exit("--brief is required for a new project")
        p.state["inputs"]["brief"] = args.brief
        p.save()
    if not p.state["inputs"].get("brief"):
        sys.exit("No brief saved for this project; start it with 'new'.")

    # ---- live dashboard (browser) + console log ----
    dcfg = cfg.get("dashboard") or {}
    p.state["run"] = {"pid": os.getpid(), "started": time.time()}
    p.save()
    sys.stdout = Tee(sys.stdout, p.dir / "console.log")
    bind(p)
    if dcfg.get("enabled", True):
        enable_web(p)
        dashboard.start(cfg["projects_dir"], dcfg.get("port", 8765),
                        os.environ.get("STUDIO_DASH_TOKEN") or dcfg.get("token") or None,
                        default=args.name)

    llm = LLM(cfg["llm"]["model"], cfg["llm"]["max_tokens"],
              cfg["llm"]["web_search_max_uses"])
    maps = cfg["comfy"].get("mappings") or {}
    comfy = Comfy(cfg["comfy"]["url"], cfg["comfy"]["poll_seconds"],
                  cfg["comfy"]["timeout_minutes"],
                  {path: maps.get(kind) or {}
                   for kind, path in cfg["comfy"]["workflows"].items()})
    ctx = Ctx(p, llm, comfy, cfg)

    i = next((n for n, s in enumerate(STAGES) if not p.approved(s)),
             len(STAGES))
    while i < len(STAGES):
        stage = STAGES[i]
        if needs_comfy(cfg, stage) and not comfy.alive():
            sys.exit(f"ComfyUI is not reachable at {cfg['comfy']['url']}. "
                     "Start it (or the SSH tunnel) and run 'resume'.")
        decision = run_stage(ctx, stage)
        if decision is None:
            i += 1
            continue
        target, items, fb = decision
        banner(f"GOING BACK TO: {target.upper()}"
               + (f" (shots {', '.join(items)})" if items else ""))
        p.event(target, "rewind", fb, frm=stage_name(i), items=items or [])
        p.rewind(target, items, fb)
        i = STAGES.index(target)

    p.set_status(None, "done", "All stages approved")
    p.event("clips", "done", "Pipeline finished")
    banner("DONE")
    print(f"Final video: {p.state.get('audio', {}).get('final') or p.dir / 'final'}"
          f"\nEdit notes: "
          f"{p.dir / '5_edit_notes.json'}\nFeedback log: {p.state_file}")

    # The dashboard runs on a daemon thread inside this process, so quitting
    # here would kill it and leave the browser frozen on the last question
    # it saw - which looks like the pipeline hung at the final gate.
    # Stay alive so the finished project stays browsable.
    if dcfg.get("enabled", True) and dcfg.get("stay_open", True):
        port = dcfg.get("port", 8765)
        print(f"\nDashboard still serving on port {port} - the run is "
              f"finished.\nPress Ctrl-C when you are done looking at it.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nDashboard stopped.")


if __name__ == "__main__":
    main()
