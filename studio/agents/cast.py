"""Agent 4 - Cast: one approved reference picture per character, location and prop.

Everything the story must keep identical gets a reference picture: the
people, the places, and the key objects (the door, the gift box, the car).
The Image agent passes these pictures to Qwen Image Edit 2511 so the same
faces, the same clearing and the same door appear in every keyframe."""
import random
import re
import shutil
from pathlib import Path

from .. import style as _style
from ..approval import gate, open_file
from ..media import image_grid
from .base import StageResult, history, rules

# Plan key -> kind. Order matters: it is the order references are offered
# to Qwen, and the first three slots are the ones that are guaranteed.
KINDS = (("locations", "location"),
         ("props", "prop"),
         ("characters", "character"))

# One reference template per kind. {style} sits next to the description so
# the art style applies to the subject, while the hard "plain backdrop"
# instruction comes last, where the model weighs it most.
REF = {
    "character": (
        "Studio photograph on a seamless plain light grey backdrop, nothing "
        "else in the frame. The subject is {name}: {desc}. {style}. Full "
        "body from head to feet, standing upright, facing the camera, "
        "relaxed neutral pose, arms slightly away from the body, face "
        "clearly visible. No scenery, no location, no props, no text. Soft "
        "even front lighting, sharp detail. Only this one character."),
    "prop": (
        "Studio product photograph on a seamless plain light grey backdrop, "
        "nothing else in the frame. The subject is {name}: {desc}. {style}. "
        "The whole object centred, three-quarter view, every surface, "
        "material, colour and marking clearly visible. No hands, no people, "
        "no scenery, no text. Soft even lighting, sharp detail."),
    "location": (
        "Establishing photograph of {name}: {desc}. {style}. Wide view of "
        "the empty place showing its layout, architecture, materials, "
        "colours and light. No people, no animals, no text. This is the "
        "set itself, to be reused in every shot there."),
}

# cast.turnaround: true -> character references are a front+back sheet, so
# shots from behind have something to copy instead of being invented.
TURNAROUND = (
    "Character turnaround sheet on a seamless plain light grey backdrop, "
    "nothing else in the frame. The SAME single character drawn twice, side "
    "by side, at the same scale and the same height: on the LEFT the front "
    "view facing the camera, on the RIGHT the back view facing directly "
    "away, showing the back of the hair, clothing and shoes. The subject is "
    "{name}: {desc}. {style}. Full body from head to feet in both views, "
    "standing upright, arms slightly away from the body, identical "
    "clothing, colours and proportions in both views. No scenery, no props, "
    "no text, no labels, no panel borders, no shadows between the views. "
    "Soft even lighting, sharp detail.")

REVISE = """You rewrite one subject's locked visual description using the
human's feedback. Keep everything the feedback does not mention. One
paragraph of concrete visual facts. For a person: age, build, skin, face,
hair, outfit with colours, distinctive features. For an object: shape,
size, material, colour, finish, hardware, wear, markings. For a place:
layout, architecture, materials, colours, vegetation, light.
Return JSON: {"description": str}"""

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "subject"


def subjects(plan):
    """[(kind, entry)] for every locked subject in the plan.

    `entry` is the dict inside the plan, not a copy, so revising a
    description here updates the plan when it is written back."""
    out = []
    for key, kind in KINDS:
        for c in (plan or {}).get(key) or []:
            if c.get("name") and c.get("locked_description"):
                out.append((kind, c))
    return out


def characters(plan):          # kept for callers that only want people
    return [c for k, c in subjects(plan) if k == "character"]


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


def _candidates(ctx, kind, c, style, n, w, h, turn, pool=None):
    """Generate n options for one subject and return the files.

    `pool` is the saved option list: pass it and each picture is published
    to it the moment it lands, so the dashboard board fills up while the
    GPU works instead of appearing all at once at the gate."""
    p, cfg = ctx.project, ctx.cfg
    name = c["name"]
    sheet_view = turn and kind == "character"
    tmpl = TURNAROUND if sheet_view else REF[kind]
    prompt = tmpl.format(name=name, desc=c["locked_description"], style=style)
    gw, gh = (max(w, h), min(w, h)) if sheet_view else (w, h)
    print(f"\n[Cast agent] {name} ({kind})"
          f"{' turnaround' if sheet_view else ''}: generating {n} options ...")
    p.set_status("cast", "working", f"generating {n} options for {name}")
    out = []
    for i in range(n):
        files = ctx.comfy.run(cfg["comfy"]["workflows"]["image"], {
            "@PROMPT": prompt, "@SEED": random.randint(1, 2**31),
            "@WIDTH": gw, "@HEIGHT": gh,
            "@FILENAME": f"studio/{p.dir.name}_cast_{slug(name)}"},
            p.dir / "candidates" / f"cast_{slug(name)}",
            label=f"{name} #{i + 1}")
        out += [f for f in files if f.suffix.lower() in IMG_EXT]
        if pool is not None:              # show it now, pick it later
            pool[name] = [str(f) for f in out]
            p.save()
    return out


def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    plan = p.read_json("2_plan.json")
    subs = subjects(plan)
    cast = p.state.setdefault("cast", {})            # name -> reference file
    kinds = p.state.setdefault("cast_kinds", {})     # name -> kind
    views = p.state.setdefault("cast_views", {})     # name -> single|turnaround
    used = p.state.setdefault("cast_descriptions", {})
    pool = p.state.setdefault("cast_options", {})    # name -> [candidate files]
    if not subs:
        return StageResult("No characters, locations or props in the plan: "
                           "nothing to cast. Keyframes use plain "
                           "text-to-image.", None, {"cast": {}})
    if not cfg.get("cast", {}).get("enabled", True):
        return StageResult("Cast stage is switched off in config.yaml.", None,
                           {"cast": {}})

    edit_wf = cfg["comfy"]["workflows"].get("edit")
    if not (edit_wf and Path(edit_wf).exists()):
        print(f"\n  WARNING: {edit_wf} is missing. References will be "
              f"generated and approved, but the Images stage will IGNORE "
              f"them and fall back to plain text-to-image.\n")

    ccfg = cfg.get("cast") or {}
    only = ccfg.get("kinds")                         # e.g. ["character"]
    if only:
        subs = [(k, c) for k, c in subs if k in only]
    w, h = cfg["defaults"]["image_size"]
    turn = ccfg.get("turnaround", False)
    n = ccfg.get("options", 3)
    # For a filtered look the plate is deliberately generated in a
    # different style from the finished video, so references follow the
    # plate, not the final treatment.
    prof = _style.load(cfg, p.state.get("style"))
    style = prof.plate_line() or plan.get("style_line", "")
    out = p.dir / "cast"
    out.mkdir(exist_ok=True)

    # ---- which subjects still need a reference -------------------------
    todo = []
    for kind, c in subs:
        name = c["name"]
        kinds[name] = kind
        if cast.get(name) and used.get(name) != c["locked_description"]:
            cast.pop(name)                           # description changed
        if feedback and (not items or name in items or slug(name) in items):
            cast.pop(name, None)
            pool.pop(name, None)                     # options are stale too
            _revise(ctx, plan, c, feedback)
        if cast.get(name) and Path(cast[name]).exists():
            continue
        todo.append((kind, c))

    # ---- phase 1: generate every option set first, no questions asked ---
    # So the GPU runs unattended and you review the whole cast in one go,
    # instead of waiting at a gate between each subject.
    if ccfg.get("batch", True) and len(todo) > 1:
        missing = [(k, c) for k, c in todo if not pool.get(c["name"])]
        if missing:
            print(f"\n[Cast agent] generating options for "
                  f"{len(missing)} subjects before asking anything ...")
            for kind, c in missing:
                _candidates(ctx, kind, c, style, n, w, h, turn, pool)
            print(f"\n[Cast agent] all options ready - now pick them.")

    # ---- phase 2: pick ---------------------------------------------------
    for kind, c in todo:
        name = c["name"]
        while True:
            sheet_view = turn and kind == "character"
            cands = [Path(x) for x in pool.get(name, []) if Path(x).exists()]
            if not cands:
                cands = _candidates(ctx, kind, c, style, n, w, h, turn, pool)
                p.save()
            sheet = image_grid(cands, p.dir / "sheets" / f"cast_{slug(name)}.png")
            open_file(sheet)
            what = {"character": "character", "prop": "object",
                    "location": "place"}[kind]
            action, val = gate(
                f"Cast: {name} ({what})",
                f"{name}: {c['locked_description']}\n"
                f"Options: {sheet}\nThis picture becomes {name}'s reference "
                f"for EVERY shot: pick the one you want to see all video long."
                + ("\nTurnaround: check it is ONE person shown twice - same "
                   "face, same clothes, front on the left and back on the "
                   "right. Two different people means retake (t)."
                   if sheet_view else ""),
                sheet,
                f"Type 1-{len(cands)} to pick, t for new options, r to change "
                f"the description, or a path to your own picture of {name}.")
            if action == "pick" and val.isdigit() and 1 <= int(val) <= len(cands):
                src = cands[int(val) - 1]
            elif action == "pick" and val.lower() == "t":
                p.event("cast", "human_pick", f"{name}: new options")
                pool.pop(name, None)                 # regenerate just this one
                continue
            elif action == "pick" and Path(val).exists():
                src = Path(val)
            elif action == "revise":
                p.log("cast", "human", f"{name}: {val}")
                p.event("cast", "agent_feedback", f"{name}: {val}")
                _revise(ctx, plan, c, val)
                pool.pop(name, None)                 # description changed
                continue
            elif action == "quit":
                raise SystemExit("Saved. Resume later.")
            else:
                print("Pick a number, t, r, or give a path to a picture.")
                continue
            dest = out / f"{slug(name)}{src.suffix.lower()}"
            shutil.copy(src, dest)
            cast[name] = str(dest)
            views[name] = "turnaround" if sheet_view else "single"
            used[name] = c["locked_description"]
            pool.pop(name, None)                     # chosen; free the list
            p.event("cast", "human_pick", f"{name}: reference chosen")
            _drop_keyframes_with(p, name)            # old keyframes, old look
            p.save()
            break

    refs = [cast[c["name"]] for _, c in subs if c["name"] in cast]
    sheet = image_grid(refs, p.dir / "sheets" / "cast.png", 360)
    open_file(sheet)
    by_kind = {}
    for kind, c in subs:
        by_kind.setdefault(kind, []).append(
            f"  {c['name']}: {cast.get(c['name'])}")
    summary = "\n".join(f"{k.title()}s:\n" + "\n".join(v)
                        for k, v in by_kind.items())
    return StageResult(f"References (used for every keyframe):\n{summary}\n\n"
                       f"All together: {sheet}", sheet,
                       {"cast": dict(cast), "cast_kinds": dict(kinds),
                        "cast_views": dict(views)}, [sheet])


def _revise(ctx, plan, c, feedback):
    p = ctx.project
    old = c["locked_description"]
    new = ctx.llm.ask_json(REVISE + rules(ctx.cfg) + "\n" + history(ctx),
                           f"Subject: {c['name']}\nDescription: {old}\n\n"
                           f"Feedback: {feedback}")["description"]
    c["locked_description"] = new
    p.write_json("2_plan.json", plan)
    _replace_description(p, old, new)


def _drop_keyframes_with(p, name):
    """A new reference means keyframes showing this subject are stale."""
    script = p.read_json("3_script.json")
    if not script:
        return
    kf, hit = p.state["keyframes"], set()
    for sh in script["shots"]:
        here = set(sh.get("characters") or []) | set(sh.get("props") or [])
        if sh.get("location"):
            here.add(sh["location"])
        if name in here:
            for which in ("start", "end"):
                if kf.pop(f"{sh['id']}_{which}", None):
                    hit.add(sh["id"])
    if hit:
        p._drop_clips(hit)
