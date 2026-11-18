"""Agent 2 - Project Planner: the complete plan / architecture."""
import json

from .base import StageResult, history, rules

RELOAD = "__reload__"
SYSTEM = """You are the Producer/Planner of a faceless AI video channel.
Turn the chosen trend into a complete, realistic production plan for this
pipeline: Z-Image Turbo keyframes -> LTX-2.5 clips -> optional 4K/60fps
finishing in ComfyUI -> assembly -> audio (voice-over with Kokoro TTS,
original music with ACE-Step, ambience with LTX audio). In audio_plan say
whether there is a voice-over and its tone, the music mood, and the
ambience. Lock the visual style, the characters, the locations and the
key props so every later agent reuses them word for word.
"props" = the objects the story returns to and that MUST look identical
every time they appear: a door, a gift box, a car, a weapon, a book.
Do not list scenery or one-off background clutter. Each locked_description
is one paragraph of concrete visual facts - for an object: shape, size,
material, colour, finish, hardware, wear and markings; for a place:
layout, architecture, materials, colours, vegetation and light. Write
them so two different artists would draw the same thing.

The human's original brief is given to you as well. Where it states a
length, a shot count or a shot length, those are requirements, not
suggestions: set target_seconds, shot_count and clip_seconds to exactly
what it asks for, even when the chosen trend would normally be told at a
different length. Where it names the beats, use those beats and do not
add, merge or drop any. If the brief asks for something you believe
cannot be told in the length it gives, still plan it at that length and
say so in risks, rather than quietly making the video longer."""

FORMAT = """Return JSON:
{"title": str, "logline": str, "audience": str,
 "platforms": [{"name": str, "aspect": "9:16|16:9", "resolution": [w,h],
                "fps": int, "target_seconds": int}],
 "style_line": str,
 "characters": [{"name": str, "locked_description": str}],
 "locations": [{"name": str, "locked_description": str}],
 "props": [{"name": str, "locked_description": str}],
 "story_beats": [str], "shot_count": int, "clip_seconds": int,
 "audio_plan": str,
 "finishing": {"upscale_4k": bool, "interpolate_60fps": bool},
 "gpu_estimate": str, "risks": [str],
 "publishing": {"caption_idea": str, "hashtags": [str]}}"""


def _locked(p, key):
    rows = "\n".join(f"  - {c['name']}: {c['locked_description']}"
                     for c in (p.get(key) or []))
    return f"{key.title()}:\n{rows}\n" if rows else ""


def summarize(p):
    chars = "\n".join(f"  - {c['name']}: {c['locked_description']}"
                      for c in p["characters"])
    beats = "\n".join(f"  {i}. {b}" for i, b in enumerate(p["story_beats"], 1))
    plats = ", ".join(f"{x['name']} {x['aspect']} {x['target_seconds']}s"
                      for x in p["platforms"])
    total = p['shot_count'] * p['clip_seconds']
    return (f"{p['title']}\n{p['logline']}\n\nPlatforms: {plats}\n"
            f"Length: {p['shot_count']} shots x {p['clip_seconds']}s "
            f"= about {total}s\n"
            f"Style: {p['style_line']}\nCharacters:\n{chars}\n"
            f"{_locked(p, 'locations')}{_locked(p, 'props')}Beats:\n{beats}"
            f"\nShots: {p['shot_count']} x {p['clip_seconds']}s | Audio: "
            f"{p['audio_plan']}\nFinishing: {p['finishing']} | GPU: "
            f"{p['gpu_estimate']}\nRisks: {'; '.join(p['risks'])}")


def run(ctx, feedback=None, items=None):
    p, d = ctx.project, ctx.cfg["defaults"]
    if feedback == RELOAD:
        data = p.read_json("2_plan.json")
    else:
        trend = p.read_json("1_trend_chosen.json")
        # The brief reaches the Trend agent, which turns it into an idea.
        # Without this line the Planner never sees what was actually asked
        # for, so a brief that says "15 seconds, five shots" is planned at
        # whatever length the trend suggests.
        brief = (p.state.get("inputs") or {}).get("brief") or ""
        prompt = (f"The human's original brief:\n{brief}\n\n"
                  f"Chosen trend:\n{json.dumps(trend, indent=2)}\n\n"
                  f"Defaults: aspect {d['aspect']}, video {d['video_size']},"
                  f" {d['fps']} fps, max clip {d['max_clip_seconds']} s.\n\n"
                  f"{history(ctx, 'plan')}\n{rules(ctx.cfg)}\n{FORMAT}")
        prev = p.read_json("2_plan.json")
        if feedback and prev:
            prompt += (f"\n\nPrevious plan:\n{json.dumps(prev)}\nRevise it "
                       f"using this feedback: {feedback}")
        elif feedback:
            prompt += f"\n\nApply this feedback: {feedback}"
        print("\n[Planner agent] writing the plan ...")
        p.set_status("plan", "working", "writing the production plan")
        data = ctx.llm.ask_json(SYSTEM, prompt)
    path = p.write_json("2_plan.json", data)
    return StageResult(summarize(data), path, data)
