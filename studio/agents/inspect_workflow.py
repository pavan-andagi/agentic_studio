"""Show the nodes of exported workflows and suggest the config mappings.

    python -m studio.inspect_workflow            # all workflows in config
    python -m studio.inspect_workflow image      # just one (image|i2v|flf2v|upscale)
"""
import json
import sys
from pathlib import Path

import yaml

RULES = [  # (tag, field names, class-name hints)
    ("@SEED", ["seed", "noise_seed"], []),
    ("@IMAGE", ["image"], ["loadimage"]),
    ("@VIDEO", ["video"], ["loadvideo"]),
    ("@WIDTH", ["width"], ["latent", "empty", "resolution", "size"]),
    ("@HEIGHT", ["height"], ["latent", "empty", "resolution", "size"]),
    ("@DURATION", ["duration", "length"], []),
    ("@FPS", ["frame_rate", "fps"], []),
    ("@FILENAME", ["filename_prefix"], ["save", "combine"]),
]


def literal(v):
    return not isinstance(v, list)


def suggest(wf, kind):
    s, notes = {}, []
    texts = [(nid, f, v) for nid, n in wf.items()
             for f, v in n.get("inputs", {}).items()
             if literal(v) and isinstance(v, str) and f in ("text", "prompt", "value")
             and len(v) > 15]
    if texts:   # the longest text box is almost always the positive prompt
        nid, f, _ = max(texts, key=lambda t: len(t[2]))
        s["@PROMPT"] = f"{nid}.{f}"
        if len(texts) > 1:
            notes.append("several text boxes: check @PROMPT is the positive one")
    loads = []
    for nid, n in sorted(wf.items()):
        cls = n.get("class_type", "").lower()
        for tag, fields, hints in RULES:
            for f in fields:
                v = n.get("inputs", {}).get(f)
                if v is None or not literal(v):
                    continue
                if hints and not any(h in cls for h in hints) and tag != "@SEED":
                    continue
                if tag == "@IMAGE":
                    loads.append(f"{nid}.{f}")
                else:
                    s.setdefault(tag, f"{nid}.{f}")
    if loads:
        s["@IMAGE"] = loads[0]
        if kind == "flf2v" and len(loads) > 1:
            s["@LAST_IMAGE"] = loads[1]
            notes.append("check which Load Image is the FIRST and which the LAST frame")
    return s, notes


def show(kind, path):
    p = Path(path)
    print(f"\n===== {kind}: {path} =====")
    if not p.exists():
        print("  missing file" + (" (optional)" if kind == "upscale" else ""))
        return None
    wf = json.loads(p.read_text())
    if "nodes" in wf and "links" in wf:
        print("  This is a normal export: use Workflow > Export (API)")
        return None
    for nid, n in sorted(wf.items(), key=lambda x: str(x[0])):
        lits = {f: (v[:50] + "..." if isinstance(v, str) and len(v) > 50 else v)
                for f, v in n.get("inputs", {}).items() if literal(v)}
        title = n.get("_meta", {}).get("title", "")
        print(f"  [{nid}] {n.get('class_type')}  '{title}'  {lits}")
    s, notes = suggest(wf, kind)
    print(f"\n  Suggested mapping for config.yaml (comfy > mappings > {kind}):")
    print("    " + kind + ": " + json.dumps(s))
    for nt in notes:
        print(f"  CHECK: {nt}")
    return s


if __name__ == "__main__":
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    wfs = cfg["comfy"]["workflows"]
    kinds = sys.argv[1:] or list(wfs)
    for k in kinds:
        show(k, wfs[k])
    print("\nPaste the suggested lines into config.yaml under comfy > mappings,"
          "\nthen run: python -m studio.check_workflows")
