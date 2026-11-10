"""Orchestrator: human reviews every stage; the Master agent turns the
human's feedback into a decision (go on, or go back to the root cause).

  python -m studio.main new  my_project --brief "..."
  python -m studio.main resume my_project
"""
import argparse
import sys
from pathlib import Path

import yaml

from .agents import clips, images, master, planner, script, trend
from .agents.base import Ctx
from .approval import STAGES, Project, banner, review_gate
from .comfy import Comfy
from .llm import LLM

AGENTS = {"trend": trend, "plan": planner, "script": script,
          "images": images, "clips": clips}
TITLES = {"trend": "1 Trend Identifier", "plan": "2 Project Planner",
          "script": "3 Script / Plot", "images": "4 Image Creation",
          "clips": "5 Clips & Editing"}


def run_stage(ctx, stage):
    """Run one stage: agent -> human review -> (if feedback) Master decides.
    Returns None to move on, or (target, items, instructions) to go back."""
    p = ctx.project
    pending = p.take_pending(stage) or {}
    feedback, items = pending.get("feedback"), pending.get("items")
    while True:
        result = AGENTS[stage].run(ctx, feedback, items)
        p.save()
        action, text = review_gate(TITLES[stage], result.summary,
                                   result.artifact)
        if action == "quit":
            raise SystemExit("Saved. Resume later.")
        if action == "edited" and stage in ("plan", "script"):
            feedback, items = planner.RELOAD, None
            continue
        if action == "approve":
            p.log(stage, "human", "Approved.")
            p.mark(stage)
            return None

        # ---- human gave feedback: the Master decides what happens ----
        p.log(stage, "human", text)
        if not ctx.cfg.get("master", {}).get("enabled", True):
            feedback, items = text, None          # no Master: redo stage
            if stage in ("images", "clips"):
                p.rewind(stage, None)
            continue
        d = master.decide(ctx, stage, result, text)
        banner("MASTER AGENT DECISION")
        print(master.describe(d))
        if ctx.cfg.get("master", {}).get("confirm_decisions", False):
            if input("\nPress Enter to carry this out, or type 'x' to "
                     "redo this stage with your feedback instead > "
                     ).strip().lower() == "x":
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
            if stage in ("images", "clips", "script"):
                p.rewind(stage, its)
            feedback, items = d["instructions"], its
            continue
        return target, its, d["instructions"]


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

    llm = LLM(cfg["llm"]["model"], cfg["llm"]["max_tokens"],
              cfg["llm"]["web_search_max_uses"])
    comfy = Comfy(cfg["comfy"]["url"], cfg["comfy"]["poll_seconds"],
                  cfg["comfy"]["timeout_minutes"])
    ctx = Ctx(p, llm, comfy, cfg)

    i = next((n for n, s in enumerate(STAGES) if not p.approved(s)),
             len(STAGES))
    while i < len(STAGES):
        stage = STAGES[i]
        if stage in ("images", "clips") and not comfy.alive():
            sys.exit(f"ComfyUI is not reachable at {cfg['comfy']['url']}. "
                     "Start it (or the SSH tunnel) and run 'resume'.")
        decision = run_stage(ctx, stage)
        if decision is None:
            i += 1
            continue
        target, items, fb = decision
        banner(f"GOING BACK TO: {target.upper()}"
               + (f" (shots {', '.join(items)})" if items else ""))
        p.rewind(target, items, fb)
        i = STAGES.index(target)

    banner("DONE")
    print(f"Final video: {p.dir / 'final'}\nEdit notes: "
          f"{p.dir / '5_edit_notes.json'}\nFeedback log: {p.state_file}")


if __name__ == "__main__":
    main()
