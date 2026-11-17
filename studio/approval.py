"""Human gates, project state, feedback log and stage rewinding."""
import json
import os
import re
import select
import subprocess
import sys
import time
import uuid
from pathlib import Path

STAGES = ["trend", "plan", "script", "cast", "images", "clips",
          "finish", "audio"]
WEB = {"project": None}      # set when the dashboard is on
ACTIVE = {"project": None}   # the project being run (for event hooks)


def bind(project):
    ACTIVE["project"] = project


def emit(kind, text="", **extra):
    """Record a message between components (Claude API, ComfyUI, ...) in the
    project timeline. Safe to call anywhere; does nothing if no project."""
    p = ACTIVE["project"]
    if p is None:
        return
    cur = p.state.get("current") or {}
    who = "master" if cur.get("status") == "master" else cur.get("stage")
    p.event(cur.get("stage"), kind, text, who=who, **extra)


def enable_web(project):
    """Let the browser dashboard answer gates (the terminal still works)."""
    WEB["project"] = project
    for f in ("pending.json", "response.json"):
        (project.dir / f).unlink(missing_ok=True)


def ask(title, summary, artifact=None, options="", kind="gate", prompt="> "):
    """One answer from the terminal OR the dashboard, whichever comes first."""
    p = WEB["project"]
    if p is None:
        return input(prompt).strip()
    gid = uuid.uuid4().hex[:8]
    (p.dir / "response.json").unlink(missing_ok=True)
    cur = p.state.get("current") or {}
    p.write_json("pending.json", {
        "id": gid, "kind": kind, "title": title, "summary": summary,
        "artifact": str(artifact) if artifact else None, "options": options,
        "stage": cur.get("stage"), "time": time.time(), "pid": os.getpid()})
    p.set_status(cur.get("stage"), "waiting", title)
    print(prompt + "(answer here, or in the dashboard) ", end="", flush=True)
    try:
        terminal = sys.stdin.isatty()
    except Exception:
        terminal = False
    resp = p.dir / "response.json"
    while True:
        if resp.exists():
            try:
                r = json.loads(resp.read_text())
            except ValueError:
                r = None
            if r and r.get("id") == gid:
                resp.unlink(missing_ok=True)
                (p.dir / "pending.json").unlink(missing_ok=True)
                print(f"[dashboard] {r.get('text', '')!r}")
                return str(r.get("text", "")).strip()
        if terminal:
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.5)
                if ready:
                    line = sys.stdin.readline()
                    if line == "":
                        terminal = False       # stdin closed: dashboard only
                    else:
                        (p.dir / "pending.json").unlink(missing_ok=True)
                        return line.strip()
            except (OSError, ValueError):
                terminal = False
        else:
            time.sleep(0.5)


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
        ans = ask(title, summary, artifact, opts, "gate")
        low = ans.lower()
        m = re.match(r"^(?:r|revise)\s+(.+)$", ans, re.I | re.S)
        if low in ("a", "approve", "y", "yes"):
            return "approve", None
        if m:                                   # "r the tiger faces left"
            return "revise", m.group(1).strip()
        if low in ("r", "revise"):
            fb = ask(title + " - feedback", "What should change?", None, "",
                     "text", "Your feedback (what should change)? > ")
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
    ans = ask(title, summary, artifact,
              "Enter = good to go; or type your feedback", "review")
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
                     ("guidance", {}), ("current", {}), ("run", {}),
                     ("finished", {}), ("finished_marks", {})):
            self.state.setdefault(k, v)

    # ---------- basics ----------
    def save(self):
        tmp = self.state_file.with_suffix(".tmp")      # atomic: the dashboard
        tmp.write_text(json.dumps(self.state, indent=2))   # never sees half a file
        os.replace(tmp, self.state_file)

    def set_status(self, stage, status, detail=""):
        """What the studio is doing right now (shown live in the dashboard)."""
        self.state["current"] = {"stage": stage, "status": status,
                                 "detail": detail, "time": time.time()}
        self.save()

    def event(self, stage, kind, text="", **extra):
        """Append to the timeline (events.jsonl) the dashboard displays."""
        rec = {"time": time.time(), "stage": stage, "kind": kind,
               "text": text, **extra}
        with open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def approved(self, stage):
        return self.state["approved"].get(stage, False)

    def mark(self, stage):
        self.state["approved"][stage] = True
        self.save()

    def write_json(self, name, data):
        p = self.dir / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, p)
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
        if target in ("trend", "plan"):
            self.state["audio"] = {}            # new story: new sound
            self.state["cast"] = {}             # and new characters
        if target == "audio":
            pass                                # sound only: keep the clips
        elif target == "finish":
            # The look is deterministic and costs no GPU: throw away the
            # graded copies and keep every plate. This is the whole reason
            # a re-grade is seconds rather than an hour.
            self.state["finished_marks"].clear()
        elif target == "cast":
            # items = character names (empty = everyone): redo their
            # pictures and every keyframe/clip they appear in
            cast = self.state.setdefault("cast", {})
            names = set(items) or set(cast)
            for n in names:
                cast.pop(n, None)
            hit = set()
            for s in self.read_json("3_script.json", {"shots": []})["shots"]:
                if names & set(s.get("characters") or []):
                    for w in ("start", "end"):
                        kf.pop(f"{s['id']}_{w}", None)
                    hit.add(s["id"])
            if hit:
                self._drop_clips(hit)
        elif target in ("trend", "plan") or (target == "script" and not items):
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
            self._drop_derived(None)
            return
        script = self.read_json("3_script.json", {"shots": []})["shots"]
        dropping, gone = False, set()
        for s in script:                    # chained shots follow along
            if s["id"] in shots:
                dropping = True
            elif s.get("start_from") != "previous_last_frame":
                dropping = False
            if dropping:
                clips.pop(s["id"], None)
                gone.add(s["id"])
        self._drop_derived(gone)

    def _drop_derived(self, shots):
        """A plate that went away takes its graded copy with it. Nothing
        here ever touches state["clips"]: the graded layer is cheap to
        rebuild and the plate is not."""
        for key in ("finished", "finished_marks"):
            d = self.state.get(key)
            if not isinstance(d, dict):
                continue
            if shots is None:
                d.clear()
            else:
                for sid in shots:
                    d.pop(sid, None)
