"""Master agent (showrunner): turns the human's feedback on a stage into
a decision - go on, or go back to the stage that caused the problem."""
import json

from ..approval import STAGES
from .base import history, rules

SYSTEM = """You are the Master agent (showrunner) of an AI video studio.
A human has just reviewed the output of one stage and given feedback.
Your job: decide what the pipeline does next, based on that feedback.

Pipeline stages and what each one owns:
- trend:  the topic / idea chosen from current trends
- plan:   format, platforms, durations, style line, locked characters
          and locations, story beats, audio plan, finishing
- script: shot list, shot modes and durations, all image and motion
          prompts, bubbles, captions
- images: the keyframe images (look, composition, consistency)
- cast:   the reference picture of each character (face, hair, outfit);
          used for every keyframe that character appears in
- clips:  the generated clips, timing, assembly, edit notes
- audio:  voice-over (narration wording, timing, voice), music,
          ambience, volume levels and the final mix. Narration wording
          is fixed HERE, not in script, so the clips are not redone.

Steps:
1. Understand exactly what the human is unhappy with.
2. Trace it to its ROOT CAUSE using the approved history: which stage
   produced the decision that led to this problem? Examples:
   - a clip moves wrongly but its prompt was fine -> clips (retake)
   - a clip is wrong because its motion prompt asks for the wrong thing
     -> script
   - a clip is wrong because the keyframe is wrong -> images
   - a character looks different in every shot because the plan never
     locked a clear description -> plan
   - the human no longer likes the idea itself -> trend
   - a character looks wrong or different from what the human wants
     (face, hair, age, outfit) -> cast, items = that character's name;
     only a single keyframe is off while the cast picture is right ->
     images, items = that shot
   - music too loud, wrong mood, a narration line too long, another
     voice -> audio (never images/clips for a sound problem)
3. Choose the EARLIEST stage that fixes the root cause with the least
   rework, and limit it to the affected shots when possible.
4. Write precise instructions for that stage's agent, combining the
   human's wishes with what must change upstream so the problem cannot
   come back downstream.
If the feedback is only a small note that later stages can handle (e.g.
"warmer music"), choose "proceed" and pass it on as notes."""

FORMAT = """Return JSON:
{"understanding": str,             # the human's concern, in one line
 "verdict": "proceed" | "revisit",
 "target_stage": "trend|plan|script|cast|images|clips|audio" | null,
 "affected_items": [str],          # shot ids like "s03"; empty = all
 "root_cause": str,
 "reasoning": str,                 # why this stage, briefly
 "instructions": str,              # for the target stage's agent
 "notes_for_next_stage": str}      # used when verdict is proceed"""


def decide(ctx, stage, result, feedback):
    allowed = STAGES[:STAGES.index(stage) + 1]
    prompt = (f"CURRENT STAGE: {stage}\nYou may only target one of: "
              f"{allowed}\n\nHUMAN FEEDBACK ON THIS STAGE:\n{feedback}\n\n"
              f"{history(ctx, upto=stage)}\n\n{rules(ctx.cfg)}\n\n"
              f"CURRENT STAGE OUTPUT:\n{json.dumps(result.data)[:60000]}"
              f"\n\n{FORMAT}")
    print("\n[Master agent] analysing your feedback ...")
    d = ctx.llm.ask_json(SYSTEM, prompt, images=result.images or None)
    if d.get("verdict") == "revisit" and d.get("target_stage") not in allowed:
        d["target_stage"] = stage
    ctx.project.write_json(f"master_{stage}.json", d)
    return d


def describe(d):
    lines = [f"Understood: {d['understanding']}"]
    if d["verdict"] == "revisit":
        items = ", ".join(d.get("affected_items") or []) or "all"
        lines += [f"Root cause: {d['root_cause']}",
                  f"Decision: go back to {d['target_stage'].upper()} "
                  f"(shots: {items})",
                  f"Why: {d['reasoning']}",
                  f"Instructions: {d['instructions']}"]
    else:
        lines += ["Decision: PROCEED to the next stage",
                  f"Notes passed on: {d.get('notes_for_next_stage', '')}"]
    return "\n".join(lines)
