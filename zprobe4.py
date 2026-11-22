#!/usr/bin/env python3
"""Is it the text encoder's output, or the model itself?

The VAE is fine and one step is already black, so NaN arrives at the
sampler. It can only come from the conditioning or from the diffusion
model's own weights.

    python zprobe4.py workflows/z_image_t2i.json

  no text at all   the zeroed conditioning is used for BOTH positive and
                   negative, so no encoder output reaches the sampler.
                   An image here - any image - means the model is sound
                   and the text encoder is producing NaN.
  one word         a minimal prompt, in case length or characters matter.
  empty string     the encoder still runs, but on nothing.
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


def post(p, o):
    r = urllib.request.Request(HOST + p, data=json.dumps(o).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


def get(p):
    return json.loads(urllib.request.urlopen(HOST + p, timeout=30).read())


def find(wf, cls=None, title=None):
    for k, n in wf.items():
        if cls and n.get("class_type") == cls:
            return k
        if title and (n.get("_meta") or {}).get("title", "").startswith(title):
            return k
    return None


def run(wf):
    try:
        pid = post("/prompt", {"prompt": wf})["prompt_id"]
    except urllib.error.HTTPError as e:
        return "rejected: " + e.read()[:140].decode(errors="replace")
    for _ in range(600):
        time.sleep(0.5)
        h = get(f"/history/{pid}")
        if pid in h:
            break
    else:
        return "timed out"
    e = h[pid]
    if (e.get("status") or {}).get("status_str") == "error":
        return "error: " + str((e.get("status") or {}).get("messages"))[:180]
    imgs = [i for o in e.get("outputs", {}).values()
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
    return ("OK   range " + " ".join(f"{lo}-{hi}" for lo, hi in ex)
            + f"  mean rgb {px.resize((1, 1)).getpixel((0, 0))}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    base = json.load(open(sys.argv[1], encoding="utf-8"))
    try:
        get("/system_stats")
    except Exception as exc:                                 # noqa: BLE001
        sys.exit(f"ComfyUI is not answering on {HOST}: {exc}")

    print(f"\n  {W}x{H}, 8 steps\n")
    for label, text, zero_pos in [
            ("no text at all", None, True),
            ("one word", "apple", False),
            ("empty string", "", False)]:
        wf = json.loads(json.dumps(base))
        lat = (find(wf, cls="EmptySD3LatentImage")
               or find(wf, cls="EmptyLatentImage"))
        wf[lat]["inputs"]["width"], wf[lat]["inputs"]["height"] = W, H
        ks = find(wf, cls="KSampler")
        wf[ks]["inputs"]["seed"] = random.randint(1, 2 ** 31)
        zo = find(wf, cls="ConditioningZeroOut")
        txt = find(wf, title="@PROMPT") or find(wf, cls="CLIPTextEncode")
        if zero_pos and zo:
            wf[ks]["inputs"]["positive"] = [zo, 0]
        elif txt:
            wf[txt]["inputs"]["text"] = text
        print(f"  {label:<18} ... ", end="", flush=True)
        try:
            print(run(wf))
        except Exception as exc:                             # noqa: BLE001
            print(f"failed: {exc}")
    print("\n  'no text at all' OK   -> the text encoder is the NaN source.\n"
          "  'no text at all' BLACK -> the diffusion model is.\n")


if __name__ == "__main__":
    main()
