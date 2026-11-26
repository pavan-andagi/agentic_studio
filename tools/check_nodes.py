"""Does ComfyUI know every node the workflows ask for?

A workflow that names a node ComfyUI has not loaded is not rejected when
it is written, or when the studio starts, or when the stage begins - it
is rejected at the moment the job is submitted, with

    invalid prompt: Node 'DWPose Estimator' not found

by which point a run has been started, a gate has been waited on, and
the cause is a node pack installed after ComfyUI last booted. This asks
the question up front instead, in two seconds.

    python3 tools/check_nodes.py                 # every workflow
    python3 tools/check_nodes.py animate         # one of them

Exit code 1 if anything is missing, so start_gui.sh can say so.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Which pack supplies what, for the ones this studio depends on. A class
# that is not here still gets reported - just without the hint.
PACKS = {
    "DWPreprocessor": "comfyui_controlnet_aux",
    "PixelPerfectResolution": "comfyui_controlnet_aux",
    "Sam2Segmentation": "ComfyUI-segment-anything-2",
    "DownloadAndLoadSAM2Model": "ComfyUI-segment-anything-2",
    "PointsEditor": "ComfyUI-KJNodes",
    "BlockifyMask": "ComfyUI-KJNodes",
    "DrawMaskOnImage": "ComfyUI-KJNodes",
    "VHS_LoadVideo": "ComfyUI-VideoHelperSuite",
    "LTXVBaseSampler": "ComfyUI-LTXVideo",
    "RIFE VFI": "ComfyUI-Frame-Interpolation",
}


def known(url):
    """The set of class_types ComfyUI has registered, or None if it is
    not answering - which is a different problem and says so."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/object_info",
                                    timeout=30) as r:
            return set(json.load(r))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def wanted(path):
    try:
        wf = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, str(e)
    out = set()
    for node in wf.values():
        if isinstance(node, dict) and node.get("class_type"):
            out.add(node["class_type"])
    return out, None


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    comfy = cfg.get("comfy") or {}
    flows = dict(comfy.get("workflows") or {})
    if len(sys.argv) > 1:
        pick = set(sys.argv[1:])
        flows = {k: v for k, v in flows.items() if k in pick}
        if not flows:
            sys.exit(f"no such workflow: {', '.join(sorted(pick))}")

    have = known(comfy.get("url", "http://127.0.0.1:8188"))
    if have is None:
        print("  ComfyUI is not answering - start it first")
        return 1

    bad = {}
    for name, rel in sorted(flows.items()):
        p = ROOT / rel
        if not p.exists():
            continue                      # optional workflows may be absent
        need, err = wanted(p)
        if err:
            print(f"  {name:<10} unreadable: {err}")
            bad[name] = set()
            continue
        missing = sorted(need - have)
        if missing:
            bad[name] = missing
            print(f"  {name:<10} MISSING {len(missing)}: "
                  f"{', '.join(missing)}")
        else:
            print(f"  {name:<10} ok ({len(need)} node types)")

    if not bad:
        return 0

    packs = sorted({PACKS[c] for m in bad.values() for c in m
                    if c in PACKS})
    print()
    if packs:
        print(f"  Those nodes come from: {', '.join(packs)}")
        print("  Install them:  bash setup/setup_pod.sh nodes swap")
    print("  Then RESTART ComfyUI - it only scans custom_nodes at boot:")
    print('    pkill -9 -f "ComfyUI/main.py"; bash setup/start_comfy.sh')
    return 1


if __name__ == "__main__":
    sys.exit(main())
