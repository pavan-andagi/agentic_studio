"""A sample project so you can see the dashboard before spending GPU time.

    python -m studio.dashboard --demo
"""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw


def _img(path, text, color, size=(270, 480)):
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    for y in range(0, size[1], 1):                    # soft gradient
        d.line([(0, y), (size[0], y)], fill=tuple(
            int(c * (0.55 + 0.45 * y / size[1])) for c in color))
    d.text((12, 12), text, fill=(255, 255, 255))
    im.save(path)


def _video(path):
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=270x480:rate=24:duration=3", "-pix_fmt",
                        "yuv420p", "-movflags", "+faststart", str(path)])
    return r.returncode == 0


SHOTS = [
    ("s01", "The tiger roars at the calm bride", "i2v", "new_keyframe", 6),
    ("s02", "The tiger softens and gives a slow blink", "i2v", "previous_last_frame", 4),
    ("s03", "She smiles; the tiger bows its head", "i2v", "new_keyframe", 5),
    ("s04", "Reveal: both look at the camera", "flf2v", "new_keyframe", 6),
]


def make_demo(root="projects", phase="images"):
    """phase: 'images' (waiting for a keyframe pick) or 'done' (finished)."""
    d = Path(root) / "demo"
    shutil.rmtree(d, ignore_errors=True)
    for sub in ("keyframes", "candidates", "clips/takes", "final", "sheets"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    now = time.time()
    wj = lambda n, o: (d / n).write_text(json.dumps(o, indent=2))
    wj("1_trends.json", {"trends": [
        {"title": "Tiger and the calm bride", "summary": "A 15 s cinematic reel: a roaring tiger meets a serene bride.",
         "why_now": "Wedding season plus 'calm vs. power' reels", "evidence": [], "format": "reel",
         "hook_idea": "Open on the roar, hold on her calm eyes", "audience": "India and diaspora",
         "fit_score": 9, "effort": "medium", "risks": ["real person lookalikes: use a generated bride"]},
        {"title": "Little Krishna shares his laddoo", "summary": "Kids' kindness story in cute 3D.",
         "why_now": "Festival season", "evidence": [], "format": "reel", "hook_idea": "A crying child alone",
         "audience": "Families", "fit_score": 8, "effort": "low", "risks": []}]})
    wj("1_trend_chosen.json", json.loads((d / "1_trends.json").read_text())["trends"][0])
    wj("2_plan.json", {"title": "The Calm Bride", "logline": "A tiger's roar cannot shake a serene bride.",
        "audience": "Reels viewers", "platforms": [{"name": "Instagram", "aspect": "9:16", "resolution": [1088, 1920], "fps": 30, "target_seconds": 20}],
        "style_line": "Ultra-realistic cinematic photograph, plain dark grey studio, soft natural light.",
        "characters": [{"name": "Tiger", "locked_description": "a large Bengal tiger in strict side profile, thick orange fur, black stripes, amber eyes"},
                       {"name": "Bride", "locked_description": "a woman in a red silk saree with jasmine garlands, hair tied up, strict side profile"}],
        "locations": [], "story_beats": [s[1] for s in SHOTS], "shot_count": len(SHOTS), "clip_seconds": 6,
        "audio_plan": "deep roar, wind, soft strings", "finishing": {"upscale_4k": False, "interpolate_60fps": True},
        "gpu_estimate": "about 45 min on an L40S", "risks": ["faces can drift: head-lock lines in every prompt"]})
    shots = [{"id": i, "beat": b, "duration": du, "mode": m, "start_from": sf,
              "start_image_prompt": None if sf == "previous_last_frame" else f"[style] {b}, tiger left, bride right",
              "end_image_prompt": "[style] both look at the camera" if m == "flf2v" else None,
              "motion_prompt": "Use the provided start image as the first frame. Her head stays upright in strict side profile. Sound: deep roar. No text, no black frames.",
              "bubble_text": None, "bubble_type": None, "edit_note": "keep the roar at normal speed" if i == "s01" else ""}
             for i, b, m, sf, du in SHOTS]
    wj("3_script.json", {"shots": shots, "voiceover": None, "captions": {
        "instagram": "Calm in the roar", "youtube_title": "The Calm Bride", "youtube_description": "AI short", "hashtags": []}})
    wj("master_script.json", {"understanding": "The tiger looks different in every shot", "verdict": "revisit",
        "target_stage": "plan", "affected_items": [], "root_cause": "The plan never locked a clear tiger description",
        "reasoning": "The plan owns character descriptions; fixing it prevents drift in every shot",
        "instructions": "Lock the tiger: Bengal, strict side profile, orange fur, black stripes, amber eyes", "notes_for_next_stage": ""})
    clock = [900.0]
    events = []

    def E(dt, st, k, x="", **e):
        clock[0] -= dt
        events.append({"time": now - 400 + (900 - clock[0]), "stage": st, "kind": k, "text": x, **e})

    M = "claude-sonnet-5-5"
    llm = lambda who, tin, tout, sec, web=False, img=0: dict(who=who, model=M, tokens_in=tin, tokens_out=tout, seconds=sec, web=web, images=img)
    E(0, "trend", "start", "Agent started", reads="channel brief")
    E(2, "trend", "llm_call", M, **llm("trend", 2100, 3400, 38.2, True))
    E(1, "trend", "output", "Chosen trend: Tiger and the calm bride")
    E(25, "trend", "approved", "You approved this stage")
    E(2, "plan", "start", "Agent started", reads="1_trend_chosen.json")
    E(2, "plan", "llm_call", M, **llm("plan", 2600, 1800, 21.4))
    E(1, "plan", "output", "The Calm Bride")
    E(10, "plan", "approved", "You approved this stage")
    E(2, "script", "start", "Agent started", reads="2_plan.json")
    E(2, "script", "llm_call", M, **llm("script", 4100, 5200, 44.7))
    E(1, "script", "output", "4 shots, about 21 s")
    E(35, "script", "human_feedback", "The tiger looks different in every shot")
    E(1, "script", "llm_call", M, **llm("master", 9800, 900, 12.9, False, 0))
    E(1, "script", "master_decision", "Go back to PLAN: The plan never locked a clear tiger description", verdict="revisit", target="plan")
    E(1, "plan", "rewind", "Lock the tiger: Bengal, strict side profile...", frm="script", items=[])
    E(2, "plan", "start", "Agent started (feedback: Lock the tiger...)", reads="1_trend_chosen.json")
    E(2, "plan", "llm_call", M, **llm("plan", 3300, 1900, 22.8))
    E(1, "plan", "output", "The Calm Bride (tiger locked)")
    E(8, "plan", "approved", "You approved this stage")
    E(2, "script", "start", "Agent started", reads="2_plan.json")
    E(2, "script", "llm_call", M, **llm("script", 4600, 5300, 46.0))
    E(1, "script", "output", "4 shots, about 21 s")
    E(14, "script", "approved", "You approved this stage")
    E(2, "images", "start", "Agent started", reads="3_script.json (image prompts)")
    for key, picks in (("s01_start", 2), ("s02_start", 1), ("s03_start", 4)):
        for i in range(picks if key != "s03_start" else 2):
            E(2, "images", "comfy_queued", f"{key} #{i + 1}", job=f"3fa9c1d{i}")
            E(9, "images", "comfy_done", f"{key} #{i + 1}", job=f"3fa9c1d{i}", seconds=8.7 + i, files=1)
        if key != "s03_start":
            E(6, "images", "human_pick", f"{key}: picked option #{picks}")
    cols = [(150, 90, 60), (60, 90, 150), (90, 150, 90), (150, 60, 120)]
    approved = {"trend": True, "plan": True, "script": True, "images": False, "clips": False}
    keyframes, clips, console = {}, {}, ["[Image agent] s01_start: generating 4 options ...", "  queued s01_start #1 (3fa9c1d2) ..."]
    cur = {"stage": "images", "status": "waiting", "detail": "Keyframe s03_start", "time": now - 20}
    for k, c in (("s01_start", cols[0]), ("s02_start", cols[1])):
        _img(d / "keyframes" / f"{k}.png", k, c)
        keyframes[k] = f"{d}/keyframes/{k}.png"
    (d / "candidates" / "s03_start").mkdir(parents=True, exist_ok=True)
    for i in range(4):
        _img(d / "candidates" / "s03_start" / f"opt{i + 1}.png", f"s03_start #{i + 1}", cols[i])
    _img(d / "sheets" / "s03_start.png", "s03_start options", (80, 80, 80), (540, 300))
    pend = {"id": "demo0001", "kind": "gate", "title": "Keyframe s03_start",
            "summary": "Shot s03: She smiles; the tiger bows its head\nPrompt: [style] She smiles; the tiger bows its head, tiger left, bride right",
            "artifact": str(d / "sheets" / "s03_start.png"), "stage": "images", "time": now, "pid": os.getpid(),
            "options": "Type 1-4 to pick, t for new options (same prompt), or a path to your own image.\n[a] approve   [r] revise with feedback"}
    if phase == "done":
        approved = {s: True for s in approved}
        for k, c in (("s03_start", cols[2]), ("s04_start", cols[3]), ("s04_end", cols[0])):
            _img(d / "keyframes" / f"{k}.png", k, c)
            keyframes[k] = f"{d}/keyframes/{k}.png"
        for sid, *_ in SHOTS:
            v = d / "clips" / f"{sid}.mp4"
            if _video(v):
                clips[sid] = str(v)
        _video(d / "final" / "demo.mp4")
        wj("5_edit_notes.json", {"edits": [{"shot": "s01", "suggestion": "Keep the roar at normal speed; trim the last 0.5 s"},
            {"shot": "s03", "suggestion": "Speed up 1.25x"}], "music": "Soft strings under the roar",
            "grade": "Cool shadows, warm highlights", "cover_frame": "s01 at 3.2 s (mouth open)", "regenerate": [], "your_notes": ["warmer music"]})
        E(1, "images", "approved", "You approved this stage")
        E(2, "clips", "start", "Agent started", reads="3_script.json + keyframes")
        for sid, *_ in SHOTS:
            E(2, "clips", "comfy_queued", sid, job="9c2e7a10")
            E(40, "clips", "comfy_done", sid, job="9c2e7a10", seconds=38.5, files=1)
            E(5, "clips", "human_pick", f"{sid}: clip approved")
        E(2, "clips", "llm_call", M, **llm("clips", 6200, 700, 15.3, False, 4))
        E(1, "clips", "output", "Final video + editing suggestions")
        E(8, "clips", "approved", "You approved this stage")
        E(1, "clips", "done", "Pipeline finished")
        cur, pend = {"stage": None, "status": "done", "detail": "All stages approved", "time": now}, None
    wj("state.json", {"approved": approved, "inputs": {"brief": "demo"}, "keyframes": keyframes, "clips": clips,
        "feedback_log": [{"time": "10:02", "stage": "script", "source": "human", "text": "The tiger looks different in every shot"},
                         {"time": "10:02", "stage": "script", "source": "master", "text": "Go back to PLAN: no locked tiger description"}],
        "pending": {}, "guidance": {}, "current": cur, "run": {"pid": os.getpid(), "started": now - 900}})
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (d / "console.log").write_text("\n".join(console) + "\n")
    if pend:
        wj("pending.json", pend)
        threading.Thread(target=_responder, args=(d, pend), daemon=True).start()
    return "demo"


def _responder(d, pend):
    """Demo only: accept any answer, log it, and ask again."""
    resp = d / "response.json"
    while True:
        time.sleep(0.5)
        if resp.exists():
            try:
                text = json.loads(resp.read_text()).get("text", "")
            except ValueError:
                continue
            resp.unlink(missing_ok=True)
            (d / "pending.json").unlink(missing_ok=True)
            with open(d / "events.jsonl", "a") as f:
                f.write(json.dumps({"time": time.time(), "stage": "images", "kind": "human_feedback",
                                    "text": f"(demo) you answered: {text}"}) + "\n")
            time.sleep(2.5)
            pend["time"] = time.time()
            (d / "pending.json").write_text(json.dumps(pend))
