#!/usr/bin/env python3
"""Where in the chain does the image become NaN?

Every sampler, every dtype and every resolution came back black, and the
workflow is wired correctly. So the question is no longer what to set -
it is which part of the chain is producing not-a-number.

    python zprobe3.py workflows/z_image_t2i.json

Four runs, each changing one thing:

  decode only   denoise 0, so the sampler does nothing and the VAE
                decodes the empty latent. A real VAE returns flat grey,
                never pure black. Black here means the VAE is at fault
                and the sampler never mattered.
  one step      denoise 1 but a single step. If eight steps are black
                and one is not, it diverges as it goes.
  no shift      ModelSamplingAuraFlow bypassed, model straight into the
                sampler. Black everywhere else but fine here means the
                shift node is the culprit.
  shift 1       the same node with a neutral value.
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


def build(base, denoise=1.0, steps=8, bypass_shift=False, shift=None):
    wf = json.loads(json.dumps(base))
    lat = find(wf, cls="EmptySD3LatentImage") or find(wf, cls="EmptyLatentImage")
    wf[lat]["inputs"]["width"], wf[lat]["inputs"]["height"] = W, H
    txt = find(wf, title="@PROMPT") or find(wf, cls="CLIPTextEncode")
    if txt:
        wf[txt]["inputs"]["text"] = PROMPT
    ks = find(wf, cls="KSampler")
    wf[ks]["inputs"].update({"seed": random.randint(1, 2 ** 31),
                             "steps": steps, "denoise": denoise})
    ms = find(wf, cls="ModelSamplingAuraFlow")
    if ms and bypass_shift:
        wf[ks]["inputs"]["model"] = wf[ms]["inputs"]["model"]  # straight through
        del wf[ms]
    elif ms and shift is not None:
        wf[ms]["inputs"]["shift"] = shift
    return wf


def run(wf):
    try:
        pid = post("/prompt", {"prompt": wf})["prompt_id"]
    except urllib.error.HTTPError as e:
        return "rejected: " + e.read()[:120].decode(errors="replace")
    for _ in range(600):
        time.sleep(0.5)
        hist = get(f"/history/{pid}")
        if pid in hist:
            break
    else:
        return "timed out"
    entry = hist[pid]
    if (entry.get("status") or {}).get("status_str") == "error":
        return "error: " + str((entry.get("status") or {}).get("messages"))[:160]
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
    px = Image.open(io.BytesIO(raw)).convert("RGB")
    ex = px.getextrema()
    if all(lo == 0 and hi == 0 for lo, hi in ex):
        return "BLACK"
    mid = px.resize((1, 1)).getpixel((0, 0))
    return ("OK   range " + " ".join(f"{lo}-{hi}" for lo, hi in ex)
            + f"  mean rgb {mid}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    base = json.load(open(sys.argv[1], encoding="utf-8"))
    try:
        get("/system_stats")
    except Exception as e:                                   # noqa: BLE001
        sys.exit(f"ComfyUI is not answering on {HOST}: {e}")

    cases = [
        ("decode only (denoise 0)", dict(denoise=0.0, steps=8)),
        ("one step", dict(steps=1)),
        ("no shift node", dict(bypass_shift=True)),
        ("shift 1", dict(shift=1.0)),
    ]
    print(f"\n  {W}x{H}\n")
    for label, kw in cases:
        print(f"  {label:<26} ... ", end="", flush=True)
        try:
            print(run(build(base, **kw)))
        except Exception as e:                               # noqa: BLE001
            print(f"failed: {e}")
    print("\n  'decode only' BLACK  -> the VAE, not the sampler.\n"
          "  'decode only' grey, everything else BLACK -> the model path.\n")


if __name__ == "__main__":
    main()
