"""Agent 4 - Cast: one approved reference image per character.

Every character in the plan gets a reference picture (full body, plain
background). The Image agent passes these pictures to Qwen Image Edit 2511
so the same faces, hair and outfits appear in every keyframe."""
import random
import re
import shutil
from pathlib import Path

from ..approval import gate, open_file
from ..media import image_grid
from .base import StageResult, history, rules

REF = ("Character reference picture of {name}: {desc}. Full body from head "
       "to feet, standing upright, facing the camera, relaxed neutral pose, "
       "arms slightly away from the body, face clearly visible. Plain light "
       "grey studio background, soft even front lighting, sharp detail. "
       "{style}. Only this one character in the picture, no text, no props.")

REVISE = """You rewrite one character's locked visual description using the
human's feedback. Keep everything the feedback does not mention. One
paragraph of concrete visual facts: age, build, skin, face, hair, outfit
with colours, distinctive features, art style if relevant.
Return JSON: {"description": str}"""

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "character"


def characters(plan):
    return [c for c in (plan or {}).get("characters") or []
            if c.get("name") and c.get("locked_description")]


def _replace_description(p, old, new):
    """The script reuses the locked description word for word: update it
    there too, so prompts and reference picture stay in step."""
    script = p.read_json("3_script.json")
    if not script or not old:
        return
    for sh in script["shots"]:
        for f in ("start_image_prompt", "end_image_prompt", "motion_prompt"):
            if sh.get(f) and old in sh[f]:
                sh[f] = sh[f].replace(old, new)
    p.write_json("3_script.json", script)


def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    plan = p.read_json("2_plan.json")
    chars = characters(plan)
    cast = p.state.setdefault("cast", {})          # name -> reference file
    used = p.state.setdefault("cast_descriptions", {})
    if not chars:
        return StageResult("No characters in the plan: nothing to cast. "
                           "Keyframes use plain text-to-image.", None,
                           {"cast": {}})
    if not cfg.get("cast", {}).get("enabled", True):
        return StageResult("Cast stage is switched off in config.yaml.", None,
                           {"cast": {}})
    w, h = cfg["defaults"]["image_size"]
    n = cfg.get("cast", {}).get("options", 3)
    style = plan.get("style_line", "")
    out = p.dir / "cast"
    out.mkdir(exist_ok=True)

    for c in chars:
        name = c["name"]
        if cast.get(name) and used.get(name) != c["locked_description"]:
            cast.pop(name)                       # description changed
        if feedback and (not items or name in items or slug(name) in items):
            cast.pop(name, None)
            _revise(ctx, plan, c, feedback)
        if cast.get(name) and Path(cast[name]).exists():
            continue
        while True:
            print(f"\n[Cast agent] {name}: generating {n} options ...")
            p.set_status("cast", "working", f"generating {n} options for {name}")
            prompt = REF.format(name=name, desc=c["locked_description"],
                                style=style)
            cands = []
            for i in range(n):
                files = ctx.comfy.run(cfg["comfy"]["workflows"]["image"], {
                    "@PROMPT": prompt, "@SEED": random.randint(1, 2**31),
                    "@WIDTH": w, "@HEIGHT": h,
                    "@FILENAME": f"studio/{p.dir.name}_cast_{slug(name)}"},
                    p.dir / "candidates" / f"cast_{slug(name)}",
                    label=f"{name} #{i + 1}")
                cands += [f for f in files if f.suffix.lower() in IMG_EXT]
            sheet = image_grid(cands, p.dir / "sheets" / f"cast_{slug(name)}.png")
            open_file(sheet)
            action, val = gate(
                f"Cast: {name}", f"{name}: {c['locked_description']}\n"
                f"Options: {sheet}\nThis picture becomes {name}'s reference "
                "for EVERY shot: pick the one you want to see all video long.",
                sheet,
                f"Type 1-{len(cands)} to pick, t for new options, r to change "
                f"the description, or a path to your own picture of {name}.")
            if action == "pick" and val.isdigit() and 1 <= int(val) <= len(cands):
                src = cands[int(val) - 1]
            elif action == "pick" and val.lower() == "t":
                p.event("cast", "human_pick", f"{name}: new options")
                continue
            elif action == "pick" and Path(val).exists():
                src = Path(val)
            elif action == "revise":
                p.log("cast", "human", f"{name}: {val}")
                p.event("cast", "agent_feedback", f"{name}: {val}")
                _revise(ctx, plan, c, val)
                continue
            elif action == "quit":
                raise SystemExit("Saved. Resume later.")
            else:
                print("Pick a number, t, r, or give a path to a picture.")
                continue
            dest = out / f"{slug(name)}{src.suffix.lower()}"
            shutil.copy(src, dest)
            cast[name] = str(dest)
            used[name] = c["locked_description"]
            p.event("cast", "human_pick", f"{name}: reference chosen")
            _drop_keyframes_with(p, name)        # old keyframes used old look
            p.save()
            break

    refs = [cast[c["name"]] for c in chars if c["name"] in cast]
    sheet = image_grid(refs, p.dir / "sheets" / "cast.png", 360)
    open_file(sheet)
    summary = "\n".join(f"{c['name']}: {cast.get(c['name'])}" for c in chars)
    return StageResult(f"Cast references (used for every keyframe):\n"
                       f"{summary}\n\nAll together: {sheet}", sheet,
                       {"cast": {k: cast[k] for k in cast}}, [sheet])


def _revise(ctx, plan, c, feedback):
    p = ctx.project
    old = c["locked_description"]
    new = ctx.llm.ask_json(REVISE + rules(ctx.cfg) + "\n" + history(ctx),
                           f"Character: {c['name']}\nDescription: {old}\n\n"
                           f"Feedback: {feedback}")["description"]
    c["locked_description"] = new
    p.write_json("2_plan.json", plan)
    _replace_description(p, old, new)


def _drop_keyframes_with(p, name):
    """A new reference means keyframes showing this character are stale."""
    script = p.read_json("3_script.json")
    if not script:
        return
    kf, hit = p.state["keyframes"], set()
    for sh in script["shots"]:
        if name in (sh.get("characters") or []):
            for which in ("start", "end"):
                if kf.pop(f"{sh['id']}_{which}", None):
                    hit.add(sh["id"])
    if hit:
        p._drop_clips(hit)
