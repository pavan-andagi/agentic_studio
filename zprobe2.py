#!/usr/bin/env python3
"""Which sampler and which weight dtype produce an actual image?

zprobe.py showed every resolution coming back black, so the size is not
the problem. What is left below the workflow is the sampler and the
precision the weights are loaded in. This runs one small generation for
each combination and reports the pixel range.

    python zprobe2.py workflows/z_image_t2i.json

A single row that is not BLACK is the answer: put that sampler or that
dtype in the workflow and the stage works. If every row is black, the
model file or the torch build is at fault and no workflow setting will
rescue it.
"""
import io
import json
import random
import sys
import time
import urllib.parse
import urllib.request

HOST = "http://127.0.0.1:8188"
W, H = 1024, 1024
PROMPT = "a bright orange sunset over the sea, high contrast"

# res_multistep is the one in the workflow. euler is the safest fallback
# for a flow-matching model; dpmpp_2m is the usual third opinion.
SAMPLERS = ["res_multistep", "euler", "dpmpp_2m", "ddim"]
# 'default' follows ComfyUI's own choice. The rest force the issue.
DTYPES = ["default", "fp8_e4m3fn", "fp16"]


def post(path, obj):
    r = urllib.request.Request(HOST + path, data=json.dumps(obj).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


def get(path):
    return json.loads(urllib.request.urlopen(HOST + path, timeout=30).read())


def find(wf, cls=None, title=None):
    for k, n in wf.items():
        if cls and n.get("class_type") == cls:
            return k
        if title and (n.get("_meta") or {}).get("title", "").startswith(title):
            return k
    return None


def run_once(base, sampler=None, dtype=None):
    wf = json.loads(json.dumps(base))
    lat = find(wf, cls="EmptySD3LatentImage") or find(wf, cls="EmptyLatentImage")
    wf[lat]["inputs"]["width"], wf[lat]["inputs"]["height"] = W, H
    txt = find(wf, title="@PROMPT") or find(wf, cls="CLIPTextEncode")
    if txt:
        wf[txt]["inputs"]["text"] = PROMPT
    ks = find(wf, cls="KSampler")
    wf[ks]["inputs"]["seed"] = random.randint(1, 2 ** 31)
    if sampler:
        wf[ks]["inputs"]["sampler_name"] = sampler
    un = find(wf, cls="UNETLoader")
    if un and dtype:
        wf[un]["inputs"]["weight_dtype"] = dtype

    try:
        pid = post("/prompt", {"prompt": wf})["prompt_id"]
    except urllib.error.HTTPError as e:
        return f"rejected: {e.read()[:120].decode(errors='replace')}"
    for _ in range(600):
        time.sleep(0.5)
        hist = get(f"/history/{pid}")
        if pid in hist:
            break
    else:
        return "timed out"

    entry = hist[pid]
    if (entry.get("status") or {}).get("status_str") == "error":
        msgs = (entry.get("status") or {}).get("messages") or []
        return f"error: {str(msgs)[:140]}"
    imgs = [i for o in entry.get("outputs", {}).values()
            for i in (o.get("images") or [])]
    if not imgs:
        return "no image"
    im = imgs[0]
    q = urllib.parse.urlencode({"filename": im["filename"],
                                "subfolder": im.get("subfolder", ""),
                                "type": im.get("type", "output")})
    raw = urllib.request.urlopen(f"{HOST}/view?{q}", timeout=60).read()
    from PIL import Image
    ex = Image.open(io.BytesIO(raw)).convert("RGB").getextrema()
    if all(lo == 0 and hi == 0 for lo, hi in ex):
        return "BLACK"
    return "OK   " + " ".join(f"{lo}-{hi}" for lo, hi in ex)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    base = json.load(open(sys.argv[1], encoding="utf-8"))
    try:
        get("/system_stats")
    except Exception as e:                                   # noqa: BLE001
        sys.exit(f"ComfyUI is not answering on {HOST}: {e}")

    print(f"\n  {W}x{H}, 8 steps, cfg 1\n")
    print("  samplers (weights as the workflow loads them)")
    for s in SAMPLERS:
        print(f"    {s:<16} ... ", end="", flush=True)
        try:
            print(run_once(base, sampler=s))
        except Exception as e:                               # noqa: BLE001
            print(f"failed: {e}")

    print("\n  weight dtypes (sampler as the workflow has it)")
    for d in DTYPES:
        print(f"    {d:<16} ... ", end="", flush=True)
        try:
            print(run_once(base, dtype=d))
        except Exception as e:                               # noqa: BLE001
            print(f"failed: {e}")
    print()


if __name__ == "__main__":
    main()
