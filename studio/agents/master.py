"""Master agent (showrunner).

Two decisions, both about where the pipeline should be.

`decide` is the one that runs during a project: the human has reviewed a
stage, and the question is whether to go on or go back, and how far.

`admit` is the one that runs before a project: somebody has analysed a
reference and wants to build from it, and the question is whether the
evidence is good enough and which stage it lets you start at. A plan
with full prompts has no need of Script; one with a keyframe for every
shot has no need of Images. Saying so is the whole point - otherwise
the run re-derives what it was already handed."""
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


# --------------------------------------------------------------- admit
ADMIT = """You are the Master agent (showrunner) of an AI video studio.
Somebody has analysed a reference video and wants to build from that
analysis. You are shown the plan, the reference frames taken from the
video, and what the studio can do. Decide whether to let it through.

Three questions, in this order.

1. Is the evidence enough to run on? A plan is enough when every shot
   has a duration, a mode, and a motion prompt, and every shot that
   needs a keyframe has either a keyframe prompt or a reference frame.
   Anything less and you say what is missing, precisely, and stop.

2. Which stage should it start at? Start at the EARLIEST stage that
   still has work to do, and no earlier:
   - a full shot list with prompts means trend, plan and script are
     already done - start at cast
   - no recurring characters means cast has nothing to make either -
     start at images
   - a usable reference frame for every shot, and the human asked to
     use them, means images is done too - start at clips
   Starting earlier than necessary throws away the analysis. Starting
   later than the evidence supports produces a run that fails at a gate
   with nothing to work from.

3. What will go wrong? Name the two or three things most likely to
   disappoint this person, from THIS plan - a shot whose motion the
   clip model will not manage, a character the keyframes will not keep
   consistent, a prompt that asks for text to be rendered. Say what to
   do about each. Vague warnings are worth nothing.

Be willing to refuse. A plan that reads well but cannot run wastes an
hour of GPU time and the human's evening."""

ADMIT_FORMAT = """Return JSON:
{"verdict": "go" | "not_yet",
 "summary": str,                    # one line, what you decided and why
 "start_stage": "trend|plan|script|cast|images|clips" | null,
 "why_that_stage": str,
 "stages_already_satisfied": [str], # what the evidence covers
 "use_reference_frames": bool,      # supply them as keyframes?
 "evidence_check": [{"item": str, "ok": bool, "note": str}],
 "missing": [{"what": str, "why_it_matters": str, "how_to_fix": str}],
 "risks": [{"what": str, "what_to_do": str}],
 "notes_for_first_stage": str}"""

# Starting later than this is not the master agent's to decide: a run
# that begins at finish has no clips to finish.
ADMIT_STAGES = ["trend", "plan", "script", "cast", "images", "clips"]


def admit(llm, cfg, plan, frames=None, prefer_reference=False):
    """Review an analysis and say where - or whether - a run starts.

    Takes the llm directly rather than a ctx, because this runs before
    there is a project to have a context about."""
    asked = ("The human asked to use the reference frames as keyframes."
             if prefer_reference else
             "The human has NOT asked to use the reference frames as "
             "keyframes; they want the shots generated from the prompts. "
             "Only set use_reference_frames true if the plan cannot work "
             "otherwise.")
    prompt = (f"THE PLAN:\n{json.dumps(plan, indent=1)[:80000]}\n\n"
              f"{asked}\n\n"
              f"Reference frames supplied: {len(frames or [])}. They are "
              f"attached in shot order.\n\n{ADMIT_FORMAT}")
    print("\n[Master agent] reviewing the plan ...")
    d = llm.ask_json(ADMIT, prompt, images=list(frames or [])[:12] or None)

    # The model is not allowed to invent a stage, nor to start a run
    # somewhere a run cannot start.
    if d.get("verdict") == "go":
        if d.get("start_stage") not in ADMIT_STAGES:
            d["start_stage"] = "cast"
            d.setdefault("why_that_stage", "")
            d["why_that_stage"] += (" (the stage it named was not one a "
                                    "run can start at, so this fell back "
                                    "to cast)")
    else:
        d["start_stage"] = None
    return d


def describe_admit(d):
    L = [f"Verdict: {'GO' if d.get('verdict') == 'go' else 'NOT YET'}",
         d.get("summary", "")]
    if d.get("verdict") == "go":
        L += [f"Starts at: {str(d.get('start_stage') or '?').upper()}",
              f"Why: {d.get('why_that_stage','')}"]
        done = d.get("stages_already_satisfied") or []
        if done:
            L.append(f"Already covered by the analysis: {', '.join(done)}")
        if d.get("use_reference_frames"):
            L.append("The reference frames go in as keyframes.")
    for m in (d.get("missing") or []):
        L.append(f"Missing - {m.get('what','')}: {m.get('how_to_fix','')}")
    for r in (d.get("risks") or []):
        L.append(f"Risk - {r.get('what','')}: {r.get('what_to_do','')}")
    return "\n".join(x for x in L if x)
