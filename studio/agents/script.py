"""Agent 3 - Script / Plot Generator: story, shot list and all prompts."""
import json

from .base import StageResult, history, rules

RELOAD = "__reload__"
SYSTEM = """You are the Screenwriter and Shot Designer. Write the story as
a shot list that the image and video agents can execute directly. Each
shot is one LTX-2.5 clip. Write complete, ready-to-run prompts."""

FORMAT = """Return JSON:
{"shots": [{"id": "s01", "beat": str, "duration": int,
  "mode": "i2v" | "flf2v",
  "start_from": "new_keyframe" | "previous_last_frame",
  "start_image_prompt": str|null, "end_image_prompt": str|null,
  "motion_prompt": str, "bubble_text": str|null,
  "bubble_type": "speech"|"thought"|null, "narration": str|null,
  "characters": [str], "location": str|null, "props": [str],
  "edit_note": str}],
 "voiceover": str|null,
 "captions": {"instagram": str, "youtube_title": str,
              "youtube_description": str, "hashtags": [str]}}
Use "previous_last_frame" when a shot continues the previous one without
a cut (then start_image_prompt is null). Shot ids: s01, s02, ...
characters = names (exactly as in the plan) of the plan characters visible
in this shot's start/end images; [] if none.
location = the name of the plan location this shot happens in (exactly as
in the plan), or null if it is nowhere specific.
props = names (exactly as in the plan) of the plan props visible in this
shot's start/end images; [] if none.
Each of these has an approved reference picture that is used to keep it
identical shot to shot, so keep the total of location + props +
characters per keyframe to about 3, and frame people so faces are not
tiny. Do NOT invent names here: only names that exist in the plan count,
anything else is ignored. Also describe the location and the props in the
image prompts as usual - the reference pictures reinforce the words, they
do not replace them.
narration = the voice-over spoken during that shot (null = silence; leave
room to breathe). At most about 2.5 words per second of the shot's
duration; a sentence may continue into the next shot. "voiceover" = all
lines joined. If the plan has no voice-over, every narration is null."""


def summarize(s):
    out = []
    for sh in s["shots"]:
        src = ("chained" if sh["start_from"] == "previous_last_frame"
               else "new keyframe")
        bub = (f'\n     {sh["bubble_type"]} bubble: "{sh["bubble_text"]}"'
               if sh.get("bubble_text") else "")
        vo = (f'\n     narration: "{sh["narration"]}"'
              if sh.get("narration") else "")
        who = (f"\n     characters: {', '.join(sh['characters'])}"
               if sh.get("characters") else "")
        where = f"\n     location: {sh['location']}" if sh.get("location") else ""
        things = (f"\n     props: {', '.join(sh['props'])}"
                  if sh.get("props") else "")
        who = who + where + things
        out.append(f"{sh['id']} [{sh['mode']}, {sh['duration']}s, {src}] "
                   f"{sh['beat']}{who}{bub}{vo}")
    total = sum(sh["duration"] for sh in s["shots"])
    return ("\n".join(out) + f"\n\nTotal: {len(s['shots'])} shots, ~{total}s"
            f"\nYouTube title: {s['captions']['youtube_title']}")


def run(ctx, feedback=None, items=None):
    p = ctx.project
    if feedback == RELOAD:
        data = p.read_json("3_script.json")
    else:
        plan = p.read_json("2_plan.json")
        prompt = (f"Approved plan:\n{json.dumps(plan, indent=2)}\n\n"
                  f"{history(ctx, 'script')}\n{rules(ctx.cfg)}\n{FORMAT}")
        prev = p.read_json("3_script.json")
        if feedback and prev:
            scope = (f" Change only shots {items}; keep the others as they "
                     f"are." if items else "")
            prompt += (f"\n\nPrevious script:\n{json.dumps(prev)}\nRevise it "
                       f"using this feedback: {feedback}.{scope}")
        elif feedback:
            prompt += f"\n\nApply this feedback: {feedback}"
        print("\n[Script agent] writing the shot list ...")
        p.set_status("script", "working", "writing the shot list and prompts")
        data = ctx.llm.ask_json(SYSTEM, prompt)
    path = p.write_json("3_script.json", data)
    return StageResult(summarize(data), path, data)
