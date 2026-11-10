"""List the @tags found in each exported workflow: python -m studio.check_workflows"""
import json
from pathlib import Path

import yaml

NEEDED = {"image": ["@PROMPT", "@SEED"],
          "i2v": ["@PROMPT", "@IMAGE", "@SEED"],
          "flf2v": ["@PROMPT", "@IMAGE", "@LAST_IMAGE", "@SEED"],
          "upscale": ["@VIDEO"]}

cfg = yaml.safe_load(Path("config.yaml").read_text())
for kind, path in cfg["comfy"]["workflows"].items():
    p = Path(path)
    if not p.exists():
        print(f"{kind:8} {path}: MISSING FILE")
        continue
    wf = json.loads(p.read_text())
    if "nodes" in wf and "links" in wf:
        print(f"{kind:8} {path}: this is a UI export - use Export (API)")
        continue
    tags = sorted({t for n in wf.values()
                   for t in n.get("_meta", {}).get("title", "").split()
                   if t.startswith("@")})
    missing = [t for t in NEEDED[kind] if t not in tags]
    print(f"{kind:8} {path}: found {tags}"
          + (f"  <-- MISSING {missing}" if missing else "  OK"))
