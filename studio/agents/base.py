"""Shared context and the production rules learned in practice."""
from dataclasses import dataclass


@dataclass
class Ctx:
    project: object      # approval.Project
    llm: object          # llm.LLM
    comfy: object        # comfy.Comfy
    cfg: dict


HOUSE_RULES = """
Production rules (follow strictly):
- Image model: Z-Image Turbo (photoreal, fast). Video model: LTX-2.5
  (image-to-video, or FLF2V = first + last frame). Clips are max
  {max_clip} s; longer moments are split into chained shots.
- Every image prompt starts with ONE shared style line from the plan and
  reuses each character's LOCKED description word for word, so characters
  stay consistent between shots.
- One camera move per clip. Interactions between characters use a static
  camera. Faces that must stay stable: say so explicitly (e.g. "her head
  stays upright in strict side profile, only her eyes and lips move").
- Never ask the video model to make objects appear from nothing: put the
  object in the keyframe or give it a visible source.
- Match duration to the action (too long = slow motion). Add "real-time
  speed, natural pace" unless slow motion is wanted.
- Motion prompts start with "Use the provided start image as the first
  frame." (I2V) and end "No text, no black frames." The clips' own
  generated sound is muted; real sound is made in the Audio stage.
- For chained shots, a shot can start from the previous clip's last frame
  (start_from = "previous_last_frame") for continuity.
- Never copy existing films, artworks, logos, celebrities or copyrighted
  characters; mythology and public-domain stories are fine. Content must
  be suitable for the stated audience (kids content: gentle, no gore).
- Speech/thought bubbles and titles are added in the editor. Narration
  (Kokoro TTS), music (ACE-Step) and ambience (LTX audio) are made in
  the Audio stage, after the clips.
"""


def rules(cfg):
    return HOUSE_RULES.format(max_clip=cfg["defaults"]["max_clip_seconds"])


@dataclass
class StageResult:
    """What every agent hands to the Master agent and the human gate."""
    summary: str                 # human-readable overview
    artifact: object             # main file (json / sheet / video)
    data: dict                   # structured output the Master reviews
    images: list = None          # contact sheets the Master can look at


def history(ctx, upto=None):
    """Everything approved so far + the feedback log, for any agent."""
    import json
    p = ctx.project
    parts = []
    for stage, f in (("trend", "1_trend_chosen.json"), ("plan", "2_plan.json"),
                     ("script", "3_script.json")):
        if upto == stage:
            break
        data = p.read_json(f)
        if data and p.approved(stage):
            parts.append(f"APPROVED {stage.upper()}:\n" + json.dumps(data))
    log = p.state["feedback_log"][-40:]
    if log:
        parts.append("FEEDBACK HISTORY (oldest first, stay consistent "
                     "with it):\n" + "\n".join(
                         f"- [{e['stage']}/{e['source']}] {e['text']}"
                         for e in log))
    g = p.state["guidance"].get(upto) if upto else None
    if g:
        parts.append(f"MASTER AGENT GUIDANCE FOR THIS STAGE: {g}")
    return "\n\n".join(parts)

