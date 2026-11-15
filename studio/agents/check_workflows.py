"""List the @tags found in each exported workflow: python -m studio.check_workflows"""
import json
from pathlib import Path

import yaml

NEEDED = {"image": ["@PROMPT", "@SEED"],
          "i2v": ["@PROMPT", "@IMAGE", "@SEED"],
          "flf2v": ["@PROMPT", "@IMAGE", "@LAST_IMAGE", "@SEED"],
          "upscale": ["@VIDEO"],
          "music": ["@PROMPT", "@SEED", "@DURATION"],
          "ambience": ["@PROMPT", "@SEED"],
          "edit": ["@PROMPT", "@IMAGE", "@IMAGE2", "@IMAGE3", "@SEED"]}
OPTIONAL = {"upscale": "only needed with upscale_4k: true",
            "music": "only needed with audio: music: enabled",
            "ambience": "only needed with audio: ambience: enabled",
            "edit": "only needed for consistent characters (Cast stage)"}

cfg = yaml.safe_load(Path("config.yaml").read_text())
maps = cfg["comfy"].get("mappings") or {}
for kind, path in cfg["comfy"]["workflows"].items():
    p = Path(path)
    if not p.exists():
        opt = f" (optional: {OPTIONAL[kind]})" if kind in OPTIONAL else ""
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
