"""Agent 4 - Image Creation: Z-Image Turbo keyframes, human picks each."""
import random
import shutil
from pathlib import Path

from ..approval import gate, open_file
from ..media import image_grid
from .base import StageResult, history, rules

REWRITE = """You rewrite one image prompt for Z-Image Turbo using the
feedback. Keep the plan's style line and the locked character
descriptions unless the feedback asks otherwise.
Return JSON: {"prompt": str}"""


def needed(script):
    for sh in script["shots"]:
        if sh["start_from"] == "new_keyframe" and sh.get("start_image_prompt"):
            yield sh, "start"
        if sh["mode"] == "flf2v" and sh.get("end_image_prompt"):
            yield sh, "end"


def rewrite(ctx, prompt, feedback):
    return ctx.llm.ask_json(REWRITE + rules(ctx.cfg) + "\n" + history(ctx),
                            f"Prompt:\n{prompt}\n\nFeedback: {feedback}"
                            )["prompt"]


def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    w, h = cfg["defaults"]["image_size"]
    n = cfg["defaults"]["images_per_keyframe"]
    script = p.read_json("3_script.json")
    done = p.state["keyframes"]
    used = p.state.setdefault("keyframe_prompts", {})

    for sh, which in needed(script):
        key, field = f"{sh['id']}_{which}", f"{which}_image_prompt"
        if key in done and used.get(key, sh[field]) != sh[field]:
            done.pop(key)                     # the script changed this image
            p._drop_clips({sh["id"]})
        if key in done:
            continue
        if feedback and (not items or sh["id"] in items or key in items):
            sh[field] = rewrite(ctx, sh[field], feedback)
            p.write_json("3_script.json", script)
        while True:
            print(f"\n[Image agent] {key}: generating {n} options ...")
            cands = []
            for i in range(n):
                files = ctx.comfy.run(cfg["comfy"]["workflows"]["image"], {
                    "@PROMPT": sh[field], "@SEED": random.randint(1, 2**31),
                    "@WIDTH": w, "@HEIGHT": h,
                    "@FILENAME": f"studio/{p.dir.name}_{key}"},
                    p.dir / "candidates" / key, label=f"{key} #{i + 1}")
                cands += [f for f in files if f.suffix.lower() in
                          (".png", ".jpg", ".jpeg", ".webp")]
            sheet = image_grid(cands, p.dir / "sheets" / f"{key}.png")
            open_file(sheet)
            action, val = gate(
                f"Keyframe {key}", f"Shot {sh['id']}: {sh['beat']}\n"
                f"Prompt: {sh[field]}\nOptions: {sheet}", sheet,
                f"Type 1-{len(cands)} to pick, t for new options (same prompt),"
                f" or a path to your own image.")
            if action == "pick" and val.isdigit() and 1 <= int(val) <= len(cands):
                src = cands[int(val) - 1]
            elif action == "pick" and val.lower() == "t":
                continue                      # same prompt, new seeds
            elif action == "pick" and Path(val).exists():
                src = Path(val)
            elif action == "revise":
                p.log("images", "human", f"{key}: {val}")
                sh[field] = rewrite(ctx, sh[field], val)
                p.write_json("3_script.json", script)
                continue
            elif action == "quit":
                raise SystemExit("Saved. Resume later.")
            else:
                print("Pick a number (or give a path), or r to revise.")
                continue
            dest = p.dir / "keyframes" / f"{key}{src.suffix}"
            shutil.copy(src, dest)
            done[key] = str(dest)
            used[key] = sh[field]
            p.save()
            break

    sheet = image_grid(list(done.values()),
                       p.dir / "sheets" / "all_keyframes.png", 360)
    open_file(sheet)
    data = {"keyframes": {k: next(s[f"{k.split('_')[1]}_image_prompt"]
                                  for s in script["shots"]
                                  if s["id"] == k.split("_")[0])
                          for k in done}}
    summary = "\n".join(f"{k}: {v}" for k, v in done.items())
    return StageResult(f"{summary}\n\nAll keyframes side by side: {sheet}",
                       sheet, data, [sheet])
