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
  "bubble_type": "speech"|"thought"|null, "edit_note": str}],
 "voiceover": str|null,
 "captions": {"instagram": str, "youtube_title": str,
              "youtube_description": str, "hashtags": [str]}}
Use "previous_last_frame" when a shot continues the previous one without
a cut (then start_image_prompt is null). Shot ids: s01, s02, ..."""


def summarize(s):
    out = []
    for sh in s["shots"]:
        src = ("chained" if sh["start_from"] == "previous_last_frame"
               else "new keyframe")
        bub = (f'\n     {sh["bubble_type"]} bubble: "{sh["bubble_text"]}"'
               if sh.get("bubble_text") else "")
        out.append(f"{sh['id']} [{sh['mode']}, {sh['duration']}s, {src}] "
                   f"{sh['beat']}{bub}")
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
