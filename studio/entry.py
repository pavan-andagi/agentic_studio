"""Where a run starts.

A project does not have to begin at the Trend agent. If you already have
a script, or a picture you want animated, the stages that would have
produced them have nothing to do - so they are filled in from what you
supplied and marked approved, and the run begins at the first stage that
still has work.

That is the whole mechanism. main.py already starts at the first
unapproved stage:

    i = next((n for n, s in enumerate(STAGES) if not p.approved(s)), ...)

so nothing in the loop changes. Pre-populate, mark, and the run begins
in the right place.

Modes, by which flag was given:

    --brief                 topic    -> trend      (the original path)
    --script f.json         script   -> cast
    --script f.json --images dir/    -> clips      (pictures supplied too)
    --image p.png --brief '...'      -> clips      (animate one picture)
    --describe '...' --brief '...'   -> cast       (draw it, then animate)

Supplying a script bypasses the Cast stage's reason to exist for any
keyframe you also supply, so cross-shot consistency becomes yours to
keep. The gate says so rather than letting you find out at Clips.
"""
import json
import shutil
from pathlib import Path

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")

# What a supplied shot must have, and what we fill in when it does not.
SHOT_DEFAULTS = {
    "mode": "i2v",
    "start_from": "new_keyframe",
    "start_image_prompt": None,
    "end_image_prompt": None,
    "motion_prompt": "",
    "bubble_text": None,
    "bubble_type": None,
    "narration": None,
    "characters": [],
    "location": None,
    "props": [],
    "overlays": [],
    "safe_area": None,
    "edit_note": "",
}


class EntryError(Exception):
    """Something supplied on the command line cannot be used."""


# ------------------------------------------------------------------ script
def _normalise(data, default_seconds):
    """Accept a hand-written script and fill in what the agents expect.

    Deliberately forgiving about everything except the two things that
    cannot be guessed: a shot needs an id and something to animate."""
    if isinstance(data, list):
        data = {"shots": data}
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        raise EntryError('the script must be a JSON object with a "shots" '
                         'list (or just the list itself)')
    shots = data["shots"]
    if not shots:
        raise EntryError("the script has no shots")

    for i, sh in enumerate(shots, 1):
        if not isinstance(sh, dict):
            raise EntryError(f"shot {i} is not an object")
        sh.setdefault("id", f"s{i:02d}")
        sh.setdefault("beat", sh.get("motion_prompt") or sh["id"])
        try:
            sh["duration"] = int(sh.get("duration") or default_seconds)
        except (TypeError, ValueError):
            raise EntryError(f"shot {sh['id']}: duration is not a number")
        for k, v in SHOT_DEFAULTS.items():
            sh.setdefault(k, list(v) if isinstance(v, list) else v)
        if sh["mode"] not in ("i2v", "flf2v"):
            raise EntryError(f"shot {sh['id']}: mode must be i2v or flf2v")
        if not sh["motion_prompt"] and not sh["start_image_prompt"]:
            raise EntryError(f"shot {sh['id']}: give it a motion_prompt, or a "
                             f"start_image_prompt so a keyframe can be made")

    ids = [s["id"] for s in shots]
    if len(set(ids)) != len(ids):
        raise EntryError(f"duplicate shot ids: {ids}")

    data.setdefault("voiceover", None)
    data.setdefault("captions", {"instagram": "", "youtube_title": "",
                                 "youtube_description": "", "hashtags": []})
    return data


# ------------------------------------------------------------------ images
def _bind_images(shots, supplied, project, aspect):
    """Attach supplied pictures to shots. Returns {keyframe key: path}.

    An explicit field in the script wins, because a filename convention
    breaks silently the first time somebody renames a file. The
    s01_start.png convention is the fallback, and plain alphabetical
    order the last resort."""
    out, by_name = {}, {}
    for f in supplied:
        by_name[f.stem.lower()] = f

    unclaimed = list(supplied)
    for sh in shots:
        for which in ("start", "end"):
            named = sh.get(f"{which}_image")
            key = f"{sh['id']}_{which}"
            src = None
            if named:                                   # explicit field
                p = Path(named)
                if not p.is_absolute():
                    p = project / p if (project / p).exists() else p
                if not p.exists():
                    raise EntryError(f"{sh['id']}: {named} does not exist")
                src = p
            elif key.lower() in by_name:                # s01_start.png
                src = by_name[key.lower()]
            elif which == "start" and sh["id"].lower() in by_name:
                src = by_name[sh["id"].lower()]
            if src:
                out[key] = src
                if src in unclaimed:
                    unclaimed.remove(src)

    # Nothing matched by name at all: fall back to file order, start
    # frames only. Better than silently using none of them.
    if not out and unclaimed:
        for sh, f in zip(shots, sorted(unclaimed)):
            out[f"{sh['id']}_start"] = f

    for key, f in out.items():
        _check_aspect(f, aspect, key)
    return out


def _check_aspect(path, aspect, key):
    """A supplied picture of the wrong shape ruins the clip quietly, so
    say it loudly instead."""
    try:
        from PIL import Image
        w, h = Image.open(path).size
    except Exception:                                    # noqa: BLE001
        return
    want = aspect[0] / aspect[1]
    got = w / h
    if abs(got - want) / want > 0.08:
        print(f"  WARNING: {key} is {w}x{h} ({got:.2f}), but the video is "
              f"{aspect[0]}x{aspect[1]} ({want:.2f}). It will be squeezed.")


# ------------------------------------------------------------------- entry
def prepare(project, cfg, script=None, image=None, images=None, brief=None,
            describe=None):
    """Fill in whatever was supplied and mark those stages approved.

    Returns a line describing what was done, or None for the normal
    topic-driven run."""
    if not (script or image or describe):
        return None

    d = cfg["defaults"]
    default_seconds = int(d.get("max_clip_seconds", 5))
    aspect = tuple(d.get("video_size", [704, 1280]))
    kf_dir = project.dir / "keyframes"
    kf_dir.mkdir(exist_ok=True)
    notes = []

    # ---- one described picture, drawn and then animated ----------------
    # Nothing is supplied here except words, so Images still has to run.
    # What is skipped is Trend, Plan and Script: there is one shot and
    # you have already said what is in it.
    if describe and not (script or image):
        look = describe.strip()
        if not look:
            raise EntryError("--describe needs a description of the picture")
        motion = (brief or "").strip()
        if not motion:
            raise EntryError("--describe needs --brief as well: the "
                             "description says what the picture IS, the "
                             "brief says what MOVES in it")
        data = _normalise({"shots": [{"id": "s01", "beat": look[:80],
                                      "start_image_prompt": look,
                                      "motion_prompt": motion,
                                      "duration": default_seconds}]},
                          default_seconds)
        supplied = {}
        notes.append(f"one described picture, drawn and then animated for "
                     f"{default_seconds}s")

    # ---- one picture, animated ----------------------------------------
    elif image and not script:
        src = Path(image)
        if not src.exists():
            raise EntryError(f"{src} does not exist")
        if src.suffix.lower() not in IMG_EXT:
            raise EntryError(f"{src} is not an image")
        motion = (brief or "").strip()
        if not motion:
            raise EntryError("--image needs --brief as well: say what should "
                             "move, e.g. --brief 'slow push in, the lantern "
                             "flickers'")
        data = _normalise({"shots": [{"id": "s01", "beat": motion[:80],
                                      "motion_prompt": motion,
                                      "duration": default_seconds}]},
                          default_seconds)
        supplied = {"s01_start": src}
        notes.append(f"one supplied picture animated for {default_seconds}s")

    # ---- a finished script, with or without pictures -------------------
    else:
        f = Path(script)
        if not f.exists():
            raise EntryError(f"{f} does not exist")
        try:
            data = _normalise(json.loads(f.read_text(encoding="utf-8")),
                              default_seconds)
        except json.JSONDecodeError as e:
            raise EntryError(f"{f} is not valid JSON: {e}")
        pics = []
        if images:
            p = Path(images)
            pics = (sorted(x for x in p.iterdir()
                           if x.suffix.lower() in IMG_EXT)
                    if p.is_dir() else [p])
            if not pics:
                raise EntryError(f"no images found in {images}")
        if image:
            pics.append(Path(image))
        supplied = _bind_images(data["shots"], pics, project.dir, aspect)
        notes.append(f"{len(data['shots'])} shots from {f.name}")
        if supplied:
            notes.append(f"{len(supplied)} supplied keyframe(s)")

    # ---- write it in --------------------------------------------------
    project.write_json("3_script.json", data)
    kf = project.state.setdefault("keyframes", {})
    for key, src in supplied.items():
        dest = kf_dir / f"{key}{src.suffix.lower()}"
        if Path(src).resolve() != dest.resolve():
            shutil.copy(src, dest)
        kf[key] = str(dest)

    # Stages whose output we just supplied have nothing left to do.
    for stage in ("trend", "plan", "script"):
        project.mark(stage)
    needed = {f"{s['id']}_start" for s in data["shots"]}
    needed |= {f"{s['id']}_end" for s in data["shots"]
               if s["mode"] == "flf2v"}
    chained = {f"{s['id']}_start" for s in data["shots"]
               if s["start_from"] == "previous_last_frame"}
    if needed - chained <= set(kf):
        project.mark("cast")             # no references to make
        project.mark("images")
        notes.append("every keyframe supplied, so Cast and Images are skipped")
    elif supplied:
        notes.append(f"{len(needed - chained - set(kf))} keyframe(s) still "
                     f"to generate")

    if describe:
        project.mark("cast")             # one shot, no cast list
        notes.append("no characters to cast, so the run starts at Images")

    # Which stage this run actually begins at. Written once, here, rather
    # than guessed later from the approvals - those accumulate as the run
    # goes, and by stage 6 you can no longer tell where it came in.
    order = ["trend", "plan", "script", "cast", "images", "clips"]
    at = next((s for s in order if not project.approved(s)), "clips")
    mode = ("describe" if describe else
            "image" if (image and not script) else
            "frames" if (script and (images or image)) else
            "script" if script else "topic")
    project.state["entry"] = {
        "script": str(script) if script else None,
        "image": str(image) if image else None,
        "images": str(images) if images else None,
        "describe": describe or None,
        "mode": mode,
        "at": at,
    }
    project.save()

    if supplied:
        notes.append("supplied pictures bypass the Cast stage, so keeping a "
                     "character or place consistent across shots is yours "
                     "to manage")
    return "; ".join(notes)
