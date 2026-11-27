"""Give every segment of a clip its own reference, in the new character.

Phase 2 of a per-segment swap. `studio.segment` found where the clip
changes and pulled one frame out of each part; this replaces the subject
in each of those frames with yours, using the same image swap that
already works on a single picture.

    python -m studio.recast seg/ --ref dog.png
    python -m studio.recast seg/ --ref front.png --ref side.png \\
        --say "Replace the dog with the golden retriever."

Why each reference is edited FROM ITS OWN FRAME, rather than generated:
the framing, the lighting, the pose and the camera angle are already
right in that frame, and they are the things a swap must not change. A
reference drawn from scratch gets the character right and everything
around it wrong, which is how a studio portrait of a sitting dog ends up
driving a shot of a dog lying down.

Why one seed and one set of references for all of them: the segments
are joined back together afterwards. Generated independently, the coat
shade and the collar drift between them, and the character visibly
changes at every seam.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from .agents.images import SWAP
from .comfy import Comfy

IMG = (".png", ".jpg", ".jpeg", ".webp")


def where(n):
    return "image 2" if n < 2 else "images 2 and 3"


def prompt_for(said, n_refs):
    said = (said or "").strip()
    if said and said[-1] not in ".!?":
        said += "."
    return SWAP.format(said=said or "Replace the main subject.",
                       where=where(n_refs))


def sheet(pairs, out, wide=1500):
    """Before and after, side by side, in segment order."""
    from PIL import Image
    if not pairs:
        return
    cols = len(pairs)
    cw = max(120, wide // (cols * 2))
    first = Image.open(pairs[0][0])
    ch = int(cw * first.height / first.width)
    board = Image.new("RGB", (cw * 2 * cols, ch), (14, 16, 22))
    for i, (before, after) in enumerate(pairs):
        board.paste(Image.open(before).convert("RGB").resize((cw, ch)),
                    (i * cw * 2, 0))
        board.paste(Image.open(after).convert("RGB").resize((cw, ch)),
                    (i * cw * 2 + cw, 0))
    board.save(out, quality=92)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", help="the folder studio.segment wrote")
    ap.add_argument("--ref", action="append", default=[], required=True,
                    help="a picture of the replacement character. Give "
                         "it twice for two views (front and side); the "
                         "edit workflow takes two")
    ap.add_argument("--say", default="",
                    help="your own sentence, e.g. 'Replace the dog with "
                         "the golden retriever'")
    ap.add_argument("--seed", type=int, default=777,
                    help="one seed for every segment, on purpose")
    ap.add_argument("--only", default="",
                    help="redo some segments: g03,g07")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    d = Path(args.dir)
    book = d / "segments.json"
    if not book.exists():
        sys.exit(f"{book} does not exist - run studio.segment first")
    data = json.loads(book.read_text(encoding="utf-8"))
    segs = data.get("segments") or []
    if not segs:
        sys.exit("that segments.json has no segments in it")

    refs = [Path(r) for r in args.ref][:2]
    for r in refs:
        if not r.exists():
            sys.exit(f"{r} does not exist")
        if r.suffix.lower() not in IMG:
            sys.exit(f"{r} is not an image")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    wf = cfg["comfy"]["workflows"].get("edit")
    if not (wf and Path(wf).exists()):
        sys.exit("config.yaml has no comfy.workflows.edit, which is the "
                 "workflow this needs")
    comfy = Comfy(cfg["comfy"].get("url", "http://127.0.0.1:8188"))

    pick = {s.strip() for s in args.only.split(",") if s.strip()}
    out = d / "cast"
    out.mkdir(exist_ok=True)
    up_refs = [comfy.upload(r) for r in refs]
    said = prompt_for(args.say, len(up_refs))
    print(f"\n  {len(segs)} segment(s), {len(up_refs)} reference view(s), "
          f"seed {args.seed}\n")

    pairs, done = [], 0
    for s in segs:
        before = Path(s["reference_image"])
        after = out / f"{s['id']}_cast.png"
        if pick and s["id"] not in pick and after.exists():
            pairs.append((before, after))
            continue
        if not before.exists():
            print(f"  {s['id']}  SKIPPED - {before} is gone")
            continue
        vals = {"@PROMPT": said, "@SEED": args.seed,
                "@IMAGE": comfy.upload(before),
                "@IMAGE2": up_refs[0] if up_refs else None,
                "@IMAGE3": up_refs[1] if len(up_refs) > 1 else None,
                "@FILENAME": f"studio/recast_{s['id']}"}
        files = [f for f in comfy.run(wf, vals, out,
                                      label=f"{s['id']} (swap)")
                 if f.suffix.lower() in IMG]
        if not files:
            print(f"  {s['id']}  no image came back")
            continue
        shutil.copy(files[0], after)
        s["cast_image"] = str(after)
        pairs.append((before, after))
        done += 1
        print(f"  {s['id']}  {s['seconds']:>4.1f}s  -> {after.name}")

    data["recast"] = {"refs": [str(r) for r in refs], "seed": args.seed,
                      "instruction": args.say}
    book.write_text(json.dumps(data, indent=2), encoding="utf-8")
    sheet(pairs, d / "contact_cast.jpg")
    print(f"\n  {done} swapped\n  {d}/contact_cast.jpg - before and "
          f"after, in order\n")
    print("  Check two things before going on: is it the same animal in "
          "every frame,\n  and does each one still sit in its own shot "
          "- same light, same framing.\n")


if __name__ == "__main__":
    main()
