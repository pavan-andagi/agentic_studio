#!/usr/bin/env python3
"""Is the text-to-image workflow black because of the size it is asked for?

Runs one workflow at several resolutions and reports the pixel range of
each result. A black frame is (0,0) on every channel: the sampler
produced NaN and the VAE clamped it. If the small sizes come back with a
real range and the large ones do not, the model is being pushed past the
resolution it was trained for and nothing is wrong with the weights.

    python zprobe.py workflows/z_image_t2i.json

Talks to ComfyUI on 127.0.0.1:8188 directly, so the studio, the
dashboard and config.yaml play no part in the answer.
"""
import json
import random
import sys
import time
import urllib.parse
import urllib.request

HOST = "http://127.0.0.1:8188"
SIZES = [(1024, 1024), (768, 1344), (832, 1472), (896, 1600), (1056, 1920)]
PROMPT = ("a supermassive black hole, bright orange accretion disk, "
          "sharp photon ring, deep space behind")


def post(path, obj):
    r = urllib.request.Request(HOST + path, data=json.dumps(obj).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


def get(path):
    return json.loads(urllib.request.urlopen(HOST + path, timeout=30).read())


def find(wf, cls=None, title=None):
    """The node id whose class or @TAG title matches."""
    for k, n in wf.items():
        if cls and n.get("class_type") == cls:
            return k
        if title and (n.get("_meta") or {}).get("title", "").startswith(title):
            return k
    return None


def run_once(wf, w, h):
    wf = json.loads(json.dumps(wf))                 # a fresh copy each time
    lat = find(wf, cls="EmptySD3LatentImage") or find(wf, cls="EmptyLatentImage")
    if not lat:
        sys.exit("no latent node in this workflow")
    wf[lat]["inputs"]["width"], wf[lat]["inputs"]["height"] = w, h
    txt = find(wf, title="@PROMPT") or find(wf, cls="CLIPTextEncode")
    if txt:
        wf[txt]["inputs"]["text"] = PROMPT
    ks = find(wf, cls="KSampler")
    if ks:
        wf[ks]["inputs"]["seed"] = random.randint(1, 2 ** 31)

    pid = post("/prompt", {"prompt": wf})["prompt_id"]
    for _ in range(600):                            # up to five minutes
        time.sleep(0.5)
        hist = get(f"/history/{pid}")
        if pid in hist:
            break
    else:
        return "timed out"

    entry = hist[pid]
    status = (entry.get("status") or {})
    if status.get("status_str") == "error":
        return "ComfyUI reported an error"
    imgs = [i for o in entry.get("outputs", {}).values()
            for i in (o.get("images") or [])]
    if not imgs:
        return "no image came back"

    im = imgs[0]
    q = urllib.parse.urlencode({"filename": im["filename"],
                                "subfolder": im.get("subfolder", ""),
                                "type": im.get("type", "output")})
    raw = urllib.request.urlopen(f"{HOST}/view?{q}", timeout=60).read()
    try:
        import io
        from PIL import Image
        ex = Image.open(io.BytesIO(raw)).convert("RGB").getextrema()
    except ImportError:
        return f"saved {len(raw)} bytes (install Pillow for the pixel range)"
    black = all(lo == 0 and hi == 0 for lo, hi in ex)
    return ("BLACK - every pixel zero" if black
            else f"OK   range {[f'{lo}-{hi}' for lo, hi in ex]}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    wf = json.load(open(sys.argv[1], encoding="utf-8"))
    try:
        get("/system_stats")
    except Exception as e:                                   # noqa: BLE001
        sys.exit(f"ComfyUI is not answering on {HOST}: {e}")

    print(f"\n  {sys.argv[1]}\n")
    for w, h in SIZES:
        mp = w * h / 1e6
        print(f"  {w:>5} x {h:<5} {mp:4.2f} MP  ... ", end="", flush=True)
        try:
            print(run_once(wf, w, h))
        except Exception as e:                               # noqa: BLE001
            print(f"failed: {e}")
    print("\n  If the small sizes are OK and the large ones are BLACK, the\n"
          "  model is being asked for more pixels than it can hold together.\n"
          "  If every size is BLACK, it is not the resolution.\n")


if __name__ == "__main__":
    main()
