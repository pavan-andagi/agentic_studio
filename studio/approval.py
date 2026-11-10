"""Human gates, project state, feedback log and stage rewinding."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STAGES = ["trend", "plan", "script", "images", "clips"]


def banner(title):
    print("\n" + "=" * 70 + f"\n  {title}\n" + "=" * 70)


def gate(title, summary, artifact=None, extra_options=None):
    """Ask the human. Returns (action, text): approve | revise | edited |
    quit | pick (free text such as a number, path, 'm', 'b')."""
    banner(f"APPROVAL NEEDED: {title}")
    print(summary)
    if artifact:
        print(f"\nFull details: {artifact}")
    opts = ("[a] approve   [r] revise with feedback   "
            "[e] I edited the file, reload it   [q] save & quit")
    if extra_options:
        opts = extra_options + "\n" + opts
    while True:
        print("\n" + opts)
        ans = input("> ").strip()
        low = ans.lower()
        if low in ("a", "approve", "y", "yes"):
            return "approve", None
        if low in ("r", "revise"):
            fb = input("Your feedback (what should change)? > ").strip()
            if fb:
                return "revise", fb
        elif low == "e":
            return "edited", None
        elif low in ("q", "quit"):
            return "quit", None
        elif ans:
            return "pick", ans


def review_gate(title, summary, artifact=None):
    """Stage review: the human looks at the output and gives feedback.
    Returns ("approve", None) | ("feedback", text) | ("edited", None) |
    ("quit", None)."""
    banner(f"YOUR REVIEW: {title}")
    print(summary)
    if artifact:
        print(f"\nFull details: {artifact}")
    print("\nPress Enter if it is good to go, or type your feedback "
          "(what is wrong / what to change).\n"
          "[e] I edited the file, reload it   [q] save & quit")
    ans = input("> ").strip()
    if not ans or ans.lower() in ("a", "ok", "approve", "y", "yes"):
        return "approve", None
    if ans.lower() == "e":
        return "edited", None
    if ans.lower() in ("q", "quit"):
        return "quit", None
    return "feedback", ans


def open_file(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.environ.get("DISPLAY"):
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class Project:
    def __init__(self, root, name):
        self.dir = Path(root) / name
        for sub in ("keyframes", "candidates", "clips", "final", "sheets"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        self.state = (json.loads(self.state_file.read_text())
                      if self.state_file.exists() else {})
        for k, v in (("approved", {}), ("inputs", {}), ("keyframes", {}),
                     ("clips", {}), ("feedback_log", []), ("pending", {}),
                     ("guidance", {})):
            self.state.setdefault(k, v)

    # ---------- basics ----------
    def save(self):
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def approved(self, stage):
        return self.state["approved"].get(stage, False)

    def mark(self, stage):
        self.state["approved"][stage] = True
        self.save()

    def write_json(self, name, data):
        p = self.dir / name
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return p

    def read_json(self, name, default=None):
        p = self.dir / name
        return json.loads(p.read_text()) if p.exists() else default

    # ---------- feedback ----------
    def log(self, stage, source, text):
        """source: human | master. Every agent sees this log."""
        self.state["feedback_log"].append({
            "time": time.strftime("%Y-%m-%d %H:%M"), "stage": stage,
            "source": source, "text": text})
        self.save()

    def take_pending(self, stage):
        return self.state["pending"].pop(stage, None)

    # ---------- going back to an earlier stage ----------
    def rewind(self, target, items=None, feedback=None):
        """Re-open `target` and everything after it. items limits an
        images/clips rewind to specific shots (e.g. ['s03'])."""
        items = items or []
        for s in STAGES[STAGES.index(target):]:
            self.state["approved"][s] = False
        kf, clips = self.state["keyframes"], self.state["clips"]
        if target in ("trend", "plan") or (target == "script" and not items):
            kf.clear()
            clips.clear()
        else:
            shots = set(items)
            if target == "images":
                for k in list(kf):
                    if not items or k.split("_")[0] in shots or k in shots:
                        kf.pop(k)
                        shots.add(k.split("_")[0])
            self._drop_clips(shots if items else None)
        if feedback:
            self.state["pending"][target] = {"feedback": feedback,
                                             "items": items}
        self.save()

    def _drop_clips(self, shots):
        clips = self.state["clips"]
        if shots is None:
            clips.clear()
            return
        script = self.read_json("3_script.json", {"shots": []})["shots"]
        dropping = False
        for s in script:                    # chained shots follow along
            if s["id"] in shots:
                dropping = True
            elif s.get("start_from") != "previous_last_frame":
                dropping = False
            if dropping:
                clips.pop(s["id"], None)
