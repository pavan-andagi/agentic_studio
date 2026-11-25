"""Work out how to rebuild something with the studio you actually have.

Two jobs, one idea. Point it at a video and it tells you the shots, the
mode each one needs, the prompts to use, and - the part that matters -
what the studio cannot do and what would have to be added. Point it at a
picture and it tells you how the picture was made and gives you a prompt
that produces something of the same quality but not the same subject.

    python -m studio.analyse video  reference.mp4
    python -m studio.analyse image  reference.png
    python -m studio.analyse image  ref.png --about "a lighthouse at dawn"

The inventory it judges against is read from config.yaml, not assumed.
If a workflow is listed but its file is missing, that counts as missing,
because a plan that depends on it would fail four stages in. The point
of the exercise is a plan you can run, so a gap named now is worth more
than a prompt that reads well.

The report is written next to the input as <name>_analysis.md and
printed. Nothing is generated and no GPU is used; this is reading.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from .llm import LLM

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
VID_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")

# How hard a cut has to be before it counts as one. 0.3 catches real
# shot changes and ignores a hand moving across the lens.
SCENE = 0.30
MAX_SHOTS = 24          # past this the LLM is guessing, not reading


# ----------------------------------------------------------- the studio
def inventory(cfg):
    """What this installation can actually do, read from its config.

    Listed-but-absent counts as absent: a plan that leans on a workflow
    file nobody exported is a plan that fails at the gate."""
    wf = (cfg.get("comfy") or {}).get("workflows") or {}
    have, gone = [], []
    for kind, path in wf.items():
        (have if Path(path).exists() else gone).append(kind)
    d = cfg.get("defaults") or {}
    return {
        "workflows_present": sorted(have),
        "workflows_listed_but_missing": sorted(gone),
        "entry_modes": [
            "topic - a brief; the studio writes and shoots it",
            "script - your shot list, keyframes generated",
            "frames - your shot list and your own keyframes",
            "image - animate one supplied picture",
            "describe - the studio draws one picture, then animates it",
            "swap - replace a character in a supplied picture",
            "swapvid - replace a character in a supplied video, "
            "keeping its motion",
        ],
        "stages": ["trend", "plan", "script", "cast", "images", "clips",
                   "finish", "render", "audio"],
        "what_each_workflow_does": {
            "image": "Z-Image Turbo, text to picture, about 1 megapixel",
            "edit": "Qwen Image Edit 2511, edits a picture using up to "
                    "two reference pictures",
            "i2v": "LTX 2.5, one keyframe to a clip",
            "flf2v": "LTX 2.5, first and last keyframe to a clip",
            "animate": "Wan 2.2 Animate, replaces a character in a video "
                       "and keeps the original motion",
            "music": "ACE-Step, text to music",
            "ambience": "LTX 2.5, text to ambient sound",
            "upscale": "RIFE and Real-ESRGAN, 4K and higher frame rate",
        },
        "also_available": [
            "Kokoro text to speech for narration",
            "deterministic look filters in the Finish stage "
            "(quantise, posterise, halftone and others)",
            "titles and captions drawn in code in the Overlays stage",
            "style profiles in config.yaml: "
            + (", ".join(sorted((cfg.get("styles") or {}))) or "none"),
        ],
        "cannot_do": [
            "camera moves that need 3D - the clip models only imagine them",
            "exact lip sync to supplied dialogue",
            "text rendered inside a generated image reliably "
            "(use the Overlays stage instead)",
            "clips longer than defaults.max_clip_seconds in one piece "
            f"(currently {d.get('max_clip_seconds')})",
            "motion transferred between two supplied videos other than "
            "through Wan Animate character swap",
        ],
        "defaults": {k: d.get(k) for k in
                     ("video_size", "image_size", "fps", "max_clip_seconds")},
    }


# ------------------------------------------------------------- the file
def facts(path):
    """Everything ffprobe will tell us, so the model reasons about a
    real file rather than an imagined one."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,bit_rate:stream=width,height,r_frame_rate,"
             "codec_name,codec_type,nb_frames",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=60).stdout
        return json.loads(out)
    except Exception as e:                                   # noqa: BLE001
        return {"error": str(e)}


def cuts(path):
    """Seconds at which the picture changes hard enough to be a cut."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", str(path), "-filter:v",
             f"select='gt(scene,{SCENE})',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600)
    except Exception:                                        # noqa: BLE001
        return []
    out = []
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            try:
                out.append(round(float(
                    line.split("pts_time:")[1].split()[0]), 2))
            except (IndexError, ValueError):
                pass
    return sorted(set(out))


def still(path, dest, at=0.5):
    """One frame from the middle of a clip, full size.

    The contact sheets are for the model; this is for the person. It is
    the frame their generated keyframe has to stand next to, and it can
    be handed straight back as a keyframe if they would rather copy the
    reference than reinterpret it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}",
             "-i", str(path), "-frames:v", "1", str(dest)],
            check=True, capture_output=True, timeout=120)
        return dest if dest.exists() else None
    except Exception:                                        # noqa: BLE001
        return None


def sheet(path, dest, every=None, count=12, cols=4):
    """One picture holding `count` frames, which is what the model sees.

    A contact sheet beats sending frames one by one: the model can see
    how the shot develops, and it costs one image instead of twelve."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    sel = (f"select='not(mod(n\\,{every}))'" if every
           else f"select='not(mod(n\\,{max(1, count)}))'")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf",
             f"{sel},scale=320:-1,tile={cols}x{-(-count // cols)}",
             "-frames:v", "1", str(dest)],
            check=True, capture_output=True, timeout=300)
        return dest if dest.exists() else None
    except Exception:                                        # noqa: BLE001
        return None


# ------------------------------------------------------------ the asking
VIDEO_SYS = """You are a technical director for an AI video studio. You
are shown contact sheets from a reference video and told exactly what
the studio can and cannot do. Produce a plan that recreates THIS video
as closely as the studio allows - same subject, same place, same
staging, same movement, same look - and be honest where it cannot.

If the human says what they want instead, keep the craft and the
treatment and change the subject to theirs.

Describe generically. Do not name real brands, logos, trademarked
characters, films or identifiable real people, and do not write a
prompt that asks for a logo or wordmark to be rendered: describe what
is physically there instead ("a charcoal wall of repeated geometric
print"). The models render text and marks badly anyway, and the
Overlays stage is where real wording belongs.

The hard part is consistency. A model given seven separately written
prompts produces seven different dogs in seven different rooms, so:

- Describe each recurring CHARACTER once, in full, in `cast`. Every
  mention of that character in a shot prompt must repeat that wording
  verbatim, not paraphrase it. Breed, build, colour, markings, eyes,
  what it wears - enough that two shots could not disagree.
- Describe each PLACE once, in full, in `locations`, the same way:
  architecture, surfaces, what is on the walls, depth, what is visible
  behind the subject.
- Put the look in `look_block` - optics, lighting, film stock, grain,
  colour. Write it ONCE and do not repeat it inside the shot prompts;
  it is appended to each of them automatically.

Then, for every shot:
- `start_image_prompt` says what the picture IS: the characters (in
  their exact words), the place (in its exact words), their position in
  frame, the framing and the lens height. It must be a description, not
  an instruction.
- `motion_prompt` says only what MOVES and how fast, plus what must
  hold still. Never restate the subject's appearance here.
- `camera` says what the camera does, in the plainest terms the clip
  model can follow: hold, slow push in, slow pan left, handheld drift.

Other rules:
- Use only the entry modes, workflows and stages in the inventory.
- Durations must respect max_clip_seconds. Split a longer beat into
  shots chained with start_from previous_last_frame.
- Anything the studio cannot do goes in `missing`, not into a prompt
  that quietly will not work. One honest gap beats five plausible shots.

`walkthrough` is the part a person follows. Write it as the steps they
actually take in this studio, in order, naming the gates by name: which
mode to start in, what to paste where, what each gate will show them,
what to look for before approving, and where it is likely to go wrong
and what to do about it. Not a summary of the plan - the doing of it.

Return JSON:
{"summary": str,
 "theme": str,
 "look": str,
 "look_block": str,
 "palette": str,
 "cast": [{"name": str, "appearance": str, "notes": str}],
 "locations": [{"name": str, "description": str}],
 "best_entry_mode": str,
 "suggested_style_profile": str or null,
 "settings": {"image_size": str, "video_size": str, "fps": int},
 "shots": [{"id": str, "seconds": int, "mode": "i2v"|"flf2v",
            "start_from": "new_keyframe"|"previous_last_frame",
            "characters": [str], "location": str,
            "start_image_prompt": str, "motion_prompt": str,
            "camera": str, "note": str}],
 "audio": {"narration": str, "music": str, "ambience": str},
 "overlays": str,
 "walkthrough": [{"step": str, "what_you_do": str, "what_to_expect": str,
                  "what_goes_wrong": str}],
 "missing": [{"what": str, "why_it_matters": str, "how_to_add": str}],
 "confidence": str}"""

IMAGE_SYS = """You are a technical director for an AI image studio. You
are shown one reference picture and told what the studio can do.

Three jobs:
1. Explain how the picture was made - subject, composition, lens,
   lighting, colour, surface detail, what gives it its quality.
2. `recreate_prompt`: a prompt that produces as close a picture to this
   one as the model can manage. Describe this picture's own subject,
   staging and background in full.
3. `reusable_prompt`: a prompt for a DIFFERENT subject at the same
   calibre - same craft, same lighting discipline, same realism. Do not
   describe the reference's subject in that one. If the human has said
   what they want, that is the subject.

Write both as descriptions of what the picture IS - the model takes
descriptions, not instructions. Name optics and light rather than
adjectives like "cinematic", which produce a grading preset and nothing
else. Keep the quality cues in `look_block`, written once; it is
appended to both prompts automatically, so do not repeat them inline.

Describe generically. Do not name real brands, logos, trademarked
characters or identifiable real people, and do not ask for a logo or
wordmark to be rendered - describe what is physically there.

Return JSON:
{"what_it_is": str,
 "subject": str,
 "composition": str,
 "background": str,
 "lighting": str,
 "optics": str,
 "colour": str,
 "surface_detail": str,
 "mood": str,
 "why_it_reads_as_real": [str],
 "look_block": str,
 "recreate_prompt": str,
 "reusable_prompt": str,
 "variations": [str],
 "prompt_notes": str,
 "settings": {"image_size": str, "steps": int},
 "missing": [{"what": str, "why_it_matters": str, "how_to_add": str}]}"""


def ask(llm, system, user, images):
    try:
        return llm.ask_json(system, user, images=images)
    except Exception as e:                                   # noqa: BLE001
        sys.exit(f"the model could not be asked: {e}")


# ------------------------------------------------------------ the report
def join(body, tail):
    """A shot prompt plus the shared look, as one block to copy.

    The look is written once and appended to every prompt rather than
    restated in each, because a look restated seven times becomes seven
    slightly different looks."""
    body = (body or "").strip()
    tail = (tail or "").strip()
    if not tail:
        return body
    if body and body[-1] not in ".!?":
        body += "."
    return f"{body} {tail}".strip()


def md_video(src, f, cut_list, plan, inv):
    L = [f"# How to rebuild {Path(src).name}", "",
         f"**{plan.get('summary','')}**", ""]
    for head, key in (("Theme", "theme"), ("Look", "look"),
                      ("Palette", "palette")):
        if plan.get(key):
            L += [f"**{head}.** {plan[key]}", ""]

    st = next((x for x in (f.get("streams") or [])
               if x.get("codec_type") == "video"), {})
    dur = (f.get("format") or {}).get("duration")
    L += ["## The reference", ""]
    if st:
        L.append(f"- {st.get('width')}x{st.get('height')}, "
                 f"{st.get('r_frame_rate')} fps"
                 + (f", {float(dur):.1f}s" if dur else ""))
    L += [f"- {len(cut_list)} hard cut(s) detected"
          + (f" at {', '.join(str(c) for c in cut_list[:12])}s"
             if cut_list else ""), ""]

    # The cast and the places come first because every shot prompt
    # below is built out of them.
    cast = plan.get("cast") or []
    if cast:
        L += ["## Cast", "",
              "Each of these is written once and repeated word for word "
              "in every shot. That repetition is what stops the subject "
              "changing between clips.", ""]
        for c in cast:
            L += [f"### {c.get('name','')}", "", "```",
                  (c.get("appearance") or "").strip(), "```", ""]
            if c.get("notes"):
                L += [f"_{c['notes']}_", ""]
    places = plan.get("locations") or []
    if places:
        L += ["## Places", ""]
        for pl in places:
            L += [f"### {pl.get('name','')}", "", "```",
                  (pl.get("description") or "").strip(), "```", ""]
    if plan.get("look_block"):
        L += ["## The look, appended to every keyframe prompt", "",
              "```", plan["look_block"].strip(), "```", ""]

    s = plan.get("settings") or {}
    walk = plan.get("walkthrough") or []
    if walk:
        L += ["## How to make it", "",
              "The steps in order, with what each gate will show you.", ""]
        for n, w in enumerate(walk, 1):
            L += [f"**{n}. {w.get('step','')}**", "",
                  w.get("what_you_do", ""), ""]
            if w.get("what_to_expect"):
                L += [f"_Expect: {w['what_to_expect']}_", ""]
            if w.get("what_goes_wrong"):
                L += [f"_If it goes wrong: {w['what_goes_wrong']}_", ""]

    L += ["## Settings for this run", "",
          f"- Picture size: {s.get('image_size','-')}",
          f"- Video size: {s.get('video_size','-')}",
          f"- Frames per second: {s.get('fps','-')}",
          f"- Entry mode: **{plan.get('best_entry_mode','-')}**",
          f"- Style profile: {plan.get('suggested_style_profile') or 'none'}",
          "", "## Shots", ""]
    look = plan.get("look_block")
    for sh in (plan.get("shots") or []):
        who = ", ".join(sh.get("characters") or []) or "-"
        L += [f"### {sh.get('id')} - {sh.get('seconds')}s, "
              f"{sh.get('mode')}, from {sh.get('start_from')}", "",
              f"Characters: {who}  |  Place: {sh.get('location','-')}"
              + (f"  |  Camera: {sh['camera']}" if sh.get("camera") else ""),
              ""]
        if sh.get("reference_frame"):
            L += [f"Frame to match: `{sh['reference_frame']}`", ""]
        if sh.get("start_image_prompt"):
            L += ["**Keyframe prompt** - paste this whole block", "",
                  "```", join(sh["start_image_prompt"], look), "```", ""]
        if sh.get("motion_prompt"):
            L += ["**Motion prompt**", "", "```",
                  sh["motion_prompt"].strip(), "```", ""]
        if sh.get("note"):
            L += [f"_{sh['note']}_", ""]

    a = plan.get("audio") or {}
    if any(a.values()):
        L += ["## Audio", ""]
        for k in ("narration", "music", "ambience"):
            if a.get(k):
                L += [f"**{k.title()}**", "", "```", a[k].strip(), "```", ""]
    if plan.get("overlays"):
        L += ["## Titles and captions", "", plan["overlays"], ""]
    L += gaps(plan, inv)
    L += ["", f"_Confidence: {plan.get('confidence','-')}_"]
    return "\n".join(L)


def md_image(src, size, plan, inv):
    L = [f"# {Path(src).name}", "", f"**{plan.get('what_it_is','')}**", ""]
    if size:
        L += [f"- {size[0]}x{size[1]}", ""]
    for head, key in (("Subject", "subject"),
                      ("Composition", "composition"),
                      ("Background", "background"),
                      ("Lighting", "lighting"), ("Optics", "optics"),
                      ("Colour", "colour"),
                      ("Surface detail", "surface_detail"),
                      ("Mood", "mood")):
        if plan.get(key):
            L += [f"**{head}.** {plan[key]}", ""]
    real = plan.get("why_it_reads_as_real") or []
    if real:
        L += ["## Why it reads as real", ""] + [f"- {x}" for x in real] + [""]
    if plan.get("recreate_prompt"):
        L += ["## A prompt that recreates this picture", "",
              "As close as the model can get. The look block is already "
              "appended, so this is one block to paste.", "", "```",
              join(plan["recreate_prompt"], plan.get("look_block")),
              "```", ""]
    L += ["## A prompt for a different subject at the same calibre", "",
          "```",
          join(plan.get("reusable_prompt", ""), plan.get("look_block")),
          "```", ""]
    if plan.get("look_block"):
        L += ["Reuse this tail on any other subject to keep the same "
              "quality:", "", "```", plan["look_block"].strip(), "```", ""]
    var = plan.get("variations") or []
    if var:
        L += ["## Other subjects at the same calibre", ""]
        for v in var:
            L += ["```", join(v, plan.get("look_block")), "```", ""]
    if plan.get("prompt_notes"):
        L += [plan["prompt_notes"], ""]
    s = plan.get("settings") or {}
    if s:
        L += [f"Settings: {s.get('image_size','-')}, "
              f"{s.get('steps','-')} steps", ""]
    L += gaps(plan, inv)
    return "\n".join(L)


def gaps(plan, inv):
    missing = plan.get("missing") or []
    out = ["## What the studio has not got", ""]
    if not missing:
        out += ["Nothing - everything above runs on what is already "
                "installed.", ""]
    else:
        for m in missing:
            out += [f"### {m.get('what','')}", "",
                    f"**Why it matters.** {m.get('why_it_matters','')}", "",
                    f"**How to add it.** {m.get('how_to_add','')}", ""]
    absent = inv.get("workflows_listed_but_missing") or []
    if absent:
        out += [f"Also, config.yaml lists these workflows but the files "
                f"are not there: {', '.join(absent)}.", ""]
    return out


# -------------------------------------------------------------- the CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("kind", choices=["video", "image"])
    ap.add_argument("path")
    ap.add_argument("--about", help="what you want to make instead, if "
                    "you already know; steers the prompt it writes")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", help="where to write the report "
                    "(default: beside the input)")
    args = ap.parse_args()

    src = Path(args.path)
    if not src.exists():
        sys.exit(f"{src} does not exist")
    cfg = yaml.safe_load(Path(args.config).read_text())
    inv = inventory(cfg)
    llm = LLM(cfg["llm"]["model"], cfg["llm"]["max_tokens"],
              cfg["llm"].get("web_search_max_uses", 0))
    work = src.parent / f".{src.stem}_analysis"
    work.mkdir(exist_ok=True)

    if args.kind == "video":
        if src.suffix.lower() not in VID_EXT:
            sys.exit(f"{src.name} is not a video")
        print("  reading the file ...")
        f = facts(src)
        refs_dir = (Path(args.out).parent if args.out else src.parent) \
            / "reference"
        frames = []
        print("  looking for cuts ...")
        cut_list = cuts(src)
        print(f"  {len(cut_list)} cut(s); building contact sheets ...")
        sheets = []
        whole = sheet(src, work / "whole.png", count=16, cols=4)
        if whole:
            sheets.append(whole)
        # One sheet per detected shot, so the model sees how each one
        # moves rather than only where the cuts are.
        bounds = [0.0] + cut_list
        for i, start in enumerate(bounds[:MAX_SHOTS]):
            end = bounds[i + 1] if i + 1 < len(bounds) else None
            seg = work / f"shot{i:02d}.mp4"
            cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(start)]
            if end:
                cmd += ["-to", str(end)]
            cmd += ["-i", str(src), "-an", str(seg)]
            try:
                subprocess.run(cmd, check=True, capture_output=True,
                               timeout=300)
            except Exception:                                # noqa: BLE001
                continue
            sh = sheet(seg, work / f"shot{i:02d}.png", count=6, cols=3)
            if sh:
                sheets.append(sh)
            # The frame a person compares their keyframe against, kept
            # beside the report rather than in the scratch directory.
            ref = still(seg, refs_dir / f"s{i + 1:02d}.png")
            if ref:
                frames.append(ref)
        print(f"  asking the model about {len(sheets)} sheet(s) ...")
        plan = ask(llm, VIDEO_SYS,
                   f"Inventory:\n{json.dumps(inv, indent=1)}\n\n"
                   f"ffprobe:\n{json.dumps(f)}\n\n"
                   f"Hard cuts at (seconds): {cut_list}\n\n"
                   f"The first sheet is the whole video; each sheet after "
                   f"it is one detected shot, in order."
                   + (f"\n\nRather than this video's own subject, the "
                      f"human wants: {args.about}. Keep the craft and "
                      f"the treatment, change the subject."
                      if args.about else
                      "\n\nRecreate this video's own subject and "
                      "staging."),
                   sheets)
        # The model sees the shots in order, so shot n of its plan is
        # frame n of the reference set. Recording the pairing means a
        # later step can use them without guessing.
        for i, shot in enumerate(plan.get("shots") or []):
            if i < len(frames):
                shot["reference_frame"] = str(frames[i])
        plan["reference_frames"] = [str(x) for x in frames]
        text = md_video(src, f, cut_list, plan, inv)
        if frames:
            print(f"  {len(frames)} reference frame(s) in {refs_dir}")
    else:
        if src.suffix.lower() not in IMG_EXT:
            sys.exit(f"{src.name} is not a picture")
        try:
            from PIL import Image
            size = Image.open(src).size
        except Exception:                                    # noqa: BLE001
            size = None
        print("  asking the model ...")
        plan = ask(llm, IMAGE_SYS,
                   f"Inventory:\n{json.dumps(inv, indent=1)}\n\n"
                   f"Picture size: {size}"
                   + (f"\n\nFor reusable_prompt the human wants: "
                      f"{args.about}" if args.about else
                      "\n\nThe human has not said what they want for "
                      "reusable_prompt, so choose a different subject "
                      "that shows off the same craft."),
                   [src])
        text = md_image(src, size, plan, inv)

    out = Path(args.out) if args.out else src.with_name(
        f"{src.stem}_analysis.md")
    out.write_text(text, encoding="utf-8")
    (out.with_suffix(".json")).write_text(
        json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{text}\n")
    print(f"  written to {out}")
    print(f"  the same thing as JSON: {out.with_suffix('.json')}")


if __name__ == "__main__":
    main()
