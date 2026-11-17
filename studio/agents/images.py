"""Agent 4 - Image Creation: Z-Image Turbo keyframes, human picks each."""
import random
import re
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


IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
ORD = ["image 1", "image 2", "image 3"]

# How each kind of reference is described to Qwen, and what must be kept.
AS = {"character": "{n} is the person in {o}",
      "prop": "the object in {o} is {n}, the exact one to use",
      "location": "{o} is the place: {n}"}
KEEP = {"character": ("Keep every person's face, hairstyle, body and outfit "
                      "exactly as in their reference image."),
        "prop": ("Keep every object exactly as in its reference image: the "
                 "same shape, size, material, colour, hardware, wear and "
                 "markings. Do not redesign it."),
        "location": ("Keep the place's architecture, layout, materials, "
                     "colours and vegetation exactly as in its reference "
                     "image; only the camera angle may change.")}


def kind_of(ctx, name):
    return (ctx.project.state.get("cast_kinds") or {}).get(name, "character")


def strip_locked(ctx, scene, names):
    """Remove the locked descriptions of subjects that are supplied as
    reference pictures.

    The Script agent copies each locked description into the image prompt
    word for word. That is right for plain text-to-image, but when the
    same subject also arrives as a reference picture the text competes
    with the image: Qwen Image Edit is an instruction model and a
    500-word scene description dilutes the references badly. Subjects
    WITHOUT a reference keep their description."""
    plan = ctx.project.read_json("2_plan.json") or {}
    for key in ("characters", "locations", "props"):
        for c in plan.get(key) or []:
            n, d = c.get("name"), c.get("locked_description")
            if n in names and d and d in scene:
                scene = scene.replace(d, n)          # keep the sentence whole
                # "Theo, Theo, stands" -> "Theo stands"
                e = re.escape(n)
                scene = re.sub(rf"\b{e}\b[,\s]+{e}\b", n, scene)
    scene = re.sub(r",\s*(?=[,.])", "", scene)
    scene = re.sub(r"\s{2,}", " ", scene)
    return re.sub(r"\s+([,.])", r"\1", scene).strip()


def cast_for(ctx, sh):
    """Subjects in this shot that have an approved reference picture.

    Ordered location, then props, then characters: Qwen takes only three
    references per pass, and the set pieces are what drift most visibly,
    so they get the guaranteed slots. People beyond that are added by a
    second pass in generate()."""
    cast = ctx.project.state.get("cast") or {}
    here = []
    if sh.get("location"):
        here.append(sh["location"])
    here += list(sh.get("props") or [])
    here += list(sh.get("characters") or [])
    seen, out = set(), []
    for c in here:
        if c in seen or not (cast.get(c) and Path(cast[c]).exists()):
            continue
        seen.add(c)
        out.append(c)
    return out


def use_edit(ctx, sh):
    wf = ctx.cfg["comfy"]["workflows"].get("edit")
    return bool(wf and Path(wf).exists() and cast_for(ctx, sh))


TURN_NOTE = ("Some reference pictures are turnaround sheets showing the "
             "SAME single character twice, front view on the left and back "
             "view on the right: that is one person, not two. Draw each "
             "character exactly once in the new picture, and use the back "
             "view only to get the back of the hair, clothing and shoes "
             "right when that character is seen from behind. Never copy "
             "the side-by-side layout.")


def _turn(ctx, names):
    v = ctx.project.state.get("cast_views") or {}
    return any(v.get(n) == "turnaround" for n in names)


def _keeps(ctx, names):
    kinds = {kind_of(ctx, n) for n in names}
    out = " ".join(KEEP[k] for k in ("location", "prop", "character")
                   if k in kinds)
    return (out + " " + TURN_NOTE) if _turn(ctx, names) else out


def edit_prompt(ctx, names, scene, first_is_scene=False):
    if first_is_scene:        # later pass: add subjects to an existing image
        who = "; ".join(AS[kind_of(ctx, n)].format(n=n, o=ORD[i + 1])
                        for i, n in enumerate(names))
        return (f"Keep image 1 exactly as it is: same scene, people, "
                f"objects, composition, camera and lighting. Add "
                f"{', '.join(names)} into it ({who}), placed as described: "
                f"{scene} {_keeps(ctx, names)} Do not copy the plain grey "
                f"reference backgrounds.")
    who = "; ".join(AS[kind_of(ctx, n)].format(n=n, o=ORD[i])
                    for i, n in enumerate(names))
    return (f"{who}. Create a new picture: {scene} {_keeps(ctx, names)} "
            f"Do not copy the plain grey reference backgrounds.")


def generate(ctx, sh, prompt, key, i, w, h):
    """One candidate: plain Z-Image, or Qwen Edit with the cast pictures.

    Qwen takes three references per pass. With `multipass: true` any
    further subjects are added by extra passes - each pass re-diffuses
    the whole frame, so crowded shots drift. With multipass off (the
    default) only the first three are referenced and the rest stay as
    text, which is less accurate per subject but far more stable."""
    p, cfg = ctx.project, ctx.cfg
    ccfg = cfg.get("cast") or {}
    out = p.dir / "candidates" / key
    seed = random.randint(1, 2**31)
    if not use_edit(ctx, sh):
        files = ctx.comfy.run(cfg["comfy"]["workflows"]["image"], {
            "@PROMPT": prompt, "@SEED": seed, "@WIDTH": w, "@HEIGHT": h,
            "@FILENAME": f"studio/{p.dir.name}_{key}"}, out,
            label=f"{key} #{i + 1}")
        return [f for f in files if f.suffix.lower() in IMG_EXT]
    cast = p.state["cast"]
    names = cast_for(ctx, sh)
    cap = max(1, min(3, int(ccfg.get("max_refs_per_keyframe", 3))))
    first, rest = names[:cap], names[cap:]
    if not ccfg.get("multipass", False):
        rest = []                 # the others stay described in the text
    # the reference pictures already carry the look of `first`
    prompt = strip_locked(ctx, prompt, first)
    refs = [ctx.comfy.upload(cast[n]) for n in first]
    vals = {"@PROMPT": edit_prompt(ctx, first, prompt), "@SEED": seed,
            "@WIDTH": w, "@HEIGHT": h,
            "@FILENAME": f"studio/{p.dir.name}_{key}",
            "@IMAGE": refs[0],
            "@IMAGE2": refs[1] if len(refs) > 1 else None,
            "@IMAGE3": refs[2] if len(refs) > 2 else None}
    files = [f for f in ctx.comfy.run(cfg["comfy"]["workflows"]["edit"], vals,
                                      out, label=f"{key} #{i + 1} (cast)")
             if f.suffix.lower() in IMG_EXT]
    while rest and files:                  # 4+ subjects: add two at a time
        add, rest = rest[:2], rest[2:]
        base = ctx.comfy.upload(files[0])
        more = [ctx.comfy.upload(cast[n]) for n in add]
        vals.update({"@PROMPT": edit_prompt(ctx, add,
                                            strip_locked(ctx, prompt, add),
                                            first_is_scene=True),
                     "@SEED": random.randint(1, 2**31), "@IMAGE": base,
                     "@IMAGE2": more[0],
                     "@IMAGE3": more[1] if len(more) > 1 else None})
        files = [f for f in ctx.comfy.run(
            cfg["comfy"]["workflows"]["edit"], vals, out,
            label=f"{key} #{i + 1} (+{', '.join(add)})")
            if f.suffix.lower() in IMG_EXT]
    return files[:1]


def signature(ctx, sh, field):
    """What this keyframe was made from: prompt + the cast pictures."""
    cast = ctx.project.state.get("cast") or {}
    refs = [f"{c}={cast[c]}" for c in cast_for(ctx, sh)] if use_edit(ctx, sh) else []
    return sh[field] + ("|" + ",".join(refs) if refs else "")


def rewrite(ctx, prompt, feedback):
    return ctx.llm.ask_json(REWRITE + rules(ctx.cfg) + "\n" + history(ctx),
                            f"Prompt:\n{prompt}\n\nFeedback: {feedback}"
                            )["prompt"]


def _candidates(ctx, sh, key, field, n, w, h, pool=None):
    """Generate n options for one keyframe and return the files.

    `pool` is the saved option list: pass it and each render is published
    the moment it lands, so the dashboard board fills up while the GPU
    works instead of appearing all at once at the gate."""
    p = ctx.project
    print(f"\n[Image agent] {key}: generating {n} options ...")
    p.set_status("images", "working", f"generating {n} options for {key}")
    who = cast_for(ctx, sh) if use_edit(ctx, sh) else []
    if who:
        print("  with references: " + ", ".join(
            f"{c} ({kind_of(ctx, c)})" for c in who))
    cands = []
    for i in range(n):
        cands += generate(ctx, sh, sh[field], key, i, w, h)
        if pool is not None:              # show it now, pick it later
            pool[key] = [str(f) for f in cands]
            p.save()
    return cands


def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    w, h = cfg["defaults"]["image_size"]
    n = cfg["defaults"]["images_per_keyframe"]
    script = p.read_json("3_script.json")
    done = p.state["keyframes"]
    used = p.state.setdefault("keyframe_prompts", {})
    pool = p.state.setdefault("keyframe_options", {})   # key -> [candidates]

    # ---- which keyframes still need picking -----------------------------
    todo = []
    for sh, which in needed(script):
        key, field = f"{sh['id']}_{which}", f"{which}_image_prompt"
        sig = signature(ctx, sh, field)
        if key in done and used.get(key, sig) != sig:
            done.pop(key)                     # the script changed this image
            pool.pop(key, None)
            p._drop_clips({sh["id"]})
        # Feedback is checked BEFORE the "already picked" skip, otherwise
        # asking to redo a keyframe you already approved does nothing.
        if feedback and (not items or sh["id"] in items or key in items):
            sh[field] = rewrite(ctx, sh[field], feedback)
            p.write_json("3_script.json", script)
            done.pop(key, None)               # this one is being redone
            pool.pop(key, None)               # options were for the old prompt
            p._drop_clips({sh["id"]})
            print(f"  redoing {key}")
        if key in done:
            continue
        todo.append((sh, which, key, field))

    # ---- phase 1: render every keyframe's options before asking anything -
    if cfg["defaults"].get("batch_keyframes", True) and len(todo) > 1:
        missing = [t for t in todo if not pool.get(t[2])]
        if missing:
            print(f"\n[Image agent] rendering options for {len(missing)} "
                  f"keyframes before asking anything ...")
            for sh, which, key, field in missing:
                _candidates(ctx, sh, key, field, n, w, h, pool)
            print("\n[Image agent] all options ready - now pick them.")

    # ---- phase 2: pick ---------------------------------------------------
    for sh, which, key, field in todo:
        while True:
            cands = [Path(x) for x in pool.get(key, []) if Path(x).exists()]
            if not cands:
                cands = _candidates(ctx, sh, key, field, n, w, h, pool)
                p.save()
            who = cast_for(ctx, sh) if use_edit(ctx, sh) else []
            sheet = image_grid(cands, p.dir / "sheets" / f"{key}.png")
            open_file(sheet)
            action, val = gate(
                f"Keyframe {key}", f"Shot {sh['id']}: {sh['beat']}\n"
                f"Prompt: {sh[field]}\n"
                + (f"Cast references used: {', '.join(who)}\n" if who else "")
                + f"Options: {sheet}", sheet,
                f"Type 1-{len(cands)} to pick, t for new options (same prompt),"
                f" or a path to your own image.")
            if action == "pick" and val.isdigit() and 1 <= int(val) <= len(cands):
                src = cands[int(val) - 1]
                p.event("images", "human_pick", f"{key}: picked option #{val}")
            elif action == "pick" and val.lower() == "t":
                p.event("images", "human_pick", f"{key}: new options, same prompt")
                pool.pop(key, None)           # re-render just this keyframe
                continue                      # same prompt, new seeds
            elif action == "pick" and Path(val).exists():
                src = Path(val)
            elif action == "revise":
                p.log("images", "human", f"{key}: {val}")
                p.event("images", "agent_feedback", f"{key}: {val}")
                sh[field] = rewrite(ctx, sh[field], val)
                p.write_json("3_script.json", script)
                pool.pop(key, None)           # options were for the old prompt
                continue
            elif action == "quit":
                raise SystemExit("Saved. Resume later.")
            else:
                print("Pick a number (or give a path), or r to revise.")
                continue
            dest = p.dir / "keyframes" / f"{key}{src.suffix}"
            shutil.copy(src, dest)
            done[key] = str(dest)
            used[key] = signature(ctx, sh, field)
            pool.pop(key, None)               # chosen; free the list
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
