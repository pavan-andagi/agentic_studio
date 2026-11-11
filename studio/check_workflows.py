"""List the @tags found in each exported workflow: python -m studio.check_workflows"""
import json
from pathlib import Path

import yaml

NEEDED = {"image": ["@PROMPT", "@SEED"],
          "i2v": ["@PROMPT", "@IMAGE", "@SEED"],
          "flf2v": ["@PROMPT", "@IMAGE", "@LAST_IMAGE", "@SEED"],
          "upscale": ["@VIDEO"]}

cfg = yaml.safe_load(Path("config.yaml").read_text())
maps = cfg["comfy"].get("mappings") or {}
for kind, path in cfg["comfy"]["workflows"].items():
    p = Path(path)
    if not p.exists():
        opt = " (optional: only needed with upscale_4k: true)" if kind == "upscale" else ""
        print(f"{kind:8} {path}: MISSING FILE{opt}")
        continue
    wf = json.loads(p.read_text())
    if "nodes" in wf and "links" in wf:
        print(f"{kind:8} {path}: this is a UI export - use Export (API)")
        continue
    tags = {t for n in wf.values()
            for t in n.get("_meta", {}).get("title", "").split()
            if t.startswith("@")}
    for tag, target in (maps.get(kind) or {}).items():
        for t in ([target] if isinstance(target, str) else target):
            if t.rsplit(".", 1)[0] in wf:
                tags.add(tag)
            else:
                print(f"{kind:8} mapping {tag} -> {t}: node not in workflow")
    tags = sorted(tags)
    missing = [t for t in NEEDED[kind] if t not in tags]
    print(f"{kind:8} {path}: found {tags}"
          + (f"  <-- MISSING {missing}" if missing else "  OK"))
