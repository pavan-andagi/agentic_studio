"""Agent 6 - Audio: voice-over (Kokoro TTS), music (ACE-Step in ComfyUI),
ambience (LTX-2.5 audio-only in ComfyUI), then the final mix.

Every layer can be switched off in config.yaml (audio:). You review each
layer, and at the end the whole mix (the normal stage review)."""
import json
import math
import random
import shutil
from pathlib import Path

from .. import tts
from ..approval import gate, open_file
from ..media import (AUDIO_EXT, duration, fit_length, mix, probe,
                     voice_track)
from .base import StageResult, history, rules

DESIGN = """You are the Sound Designer of a faceless short-video channel.
Design the sound for the approved video: one music bed and one continuous
ambience track. The voice-over is separate (the script's narration).
- music_prompt: comma-separated style tags for the ACE-Step music model:
  genre, instruments, mood, tempo, "instrumental, no vocals". Original
  music only: never name existing songs, films or composers.
- ambience_prompt: one continuous background soundscape for the LTX audio
  model (environment sounds only: no music, no speech, no sudden hits).
Return JSON: {"music_prompt": str, "bpm": int, "ambience_prompt": str,
 "notes": str}"""

REVISE = """You are the Sound Designer. The human gave feedback on the
sound. Decide what to change. Levels are dB relative to the voice (0):
music usually -20..-10, ambience -26..-14. Change narration only if the
feedback is about the spoken words or their timing; a line may hold at
most about {wps} words per second of its shot (null = silence).
Return JSON: {{"redo": ["voice"|"music"|"ambience"], "music_prompt": str|null,
 "ambience_prompt": str|null, "levels": {{"music": num, "ambience": num}}|null,
 "voice": str|null, "speed": num|null,
 "narration": [{{"id": "s01", "text": str|null}}]|null, "summary": str}}
Use null for anything that stays as it is. "redo" lists the layers that
must be generated again (a level change alone needs no redo)."""

LINES = """Rewrite the voice-over narration using the feedback. One line per
shot (or null for silence). A line may hold at most about {wps} words per
second of its shot's duration, and may continue a sentence from the
previous shot. Keep the facts and the tone of the approved script.
Return JSON: {{"narration": [{{"id": "s01", "text": str|null}}]}}"""

PROMPT = """Rewrite this {what} prompt using the feedback. Keep what worked.
Return JSON: {{"prompt": str}}"""


# ------------------------------------------------------------ timing
def timeline(ctx, shots):
    """Start time and length of every shot in the assembled video."""
    p, d = ctx.project, ctx.cfg["defaults"]
    done, t, out = p.state["clips"], 0.0, {}
    for i, s in enumerate(shots):
        clip = Path(done[s["id"]])
        k4 = clip.with_name(f"{s['id']}_4k.mp4")
        if d.get("upscale_4k") and k4.exists():
            clip = k4
        info = probe(clip)
        length = info["duration"]
        if s.get("start_from") == "previous_last_frame" and i > 0:
            length -= 1 / max(info["fps"], 1)           # dropped frame
        out[s["id"]] = (round(t, 3), round(length, 3))
        t += length
    return out


def narration(script):
    """Per-shot lines; falls back to the old single `voiceover` string."""
    lines = {s["id"]: (s.get("narration") or "").strip() or None
             for s in script["shots"]}
    if not any(lines.values()) and script.get("voiceover"):
        lines[script["shots"][0]["id"]] = script["voiceover"].strip()
    return lines


def _pick_files(files):
    return [f for f in files if f.suffix.lower() in AUDIO_EXT]


# ------------------------------------------------------------ layers
def _voice(ctx, script, times, total, acfg, st, adir, feedback=None):
    """Voice-over: speak each shot's line, place it at the shot's start."""
    p, vc = ctx.project, acfg.get("voiceover", {})
    wps = vc.get("max_words_per_second", 2.5)
    while True:
        if st.get("voice_file"):                         # your own recording
            track = Path(st["voice_file"])
            info = f"Your own voice-over file: {track}"
        else:
            lines = narration(script)
            if not any(lines.values()):
                return None, "No narration in the script (no voice-over)."
            voice = st.get("voice") or vc.get("voice", "am_michael")
            speed = st.get("speed") or vc.get("speed", 1.0)
            p.set_status("audio", "working", f"speaking the narration ({voice})")
            print(f"\n[Audio agent] voice-over with Kokoro ({voice}) ...")
            segs, rows, cursor = [], [], 0.0
            for s in script["shots"]:
                text = lines.get(s["id"])
                if not text:
                    continue
                f = tts.say(text, adir / "voice", voice, speed,
                            vc.get("lang"), vc.get("device", "cpu"))
                start, length = times[s["id"]]
                at = max(start + vc.get("lead_in", 0.3), cursor + 0.15)
                ln = duration(f)
                cursor = at + ln
                over = cursor - (start + length)
                segs.append((f, at))
                rows.append(f"{s['id']} @ {at:5.1f}s ({ln:.1f}s"
                            + (f", runs {over:.1f}s into the next shot"
                               if over > 0.2 else "") + f"): {text}")
            if cursor > total:
                rows.append(f"WARNING: the voice-over ends at {cursor:.1f}s "
                            f"but the video is {total:.1f}s; shorten lines.")
            track = voice_track(segs, total, adir / "voice_track.wav")
            info = f"Voice: {voice}, speed {speed}\n" + "\n".join(rows)
        key = (str(track), info)
        if st.get("voice_ok") == list(key) and not feedback:
            return track, info                      # already approved
        prev = mix(st["video"], adir / "preview_voice.mp4", voice=track)
        open_file(prev)
        action, val = gate(
            "Voice-over", f"{info}\nPreview (voice only): {prev}", prev,
            "[a] approve   v <voice id> = another voice (e.g. v bm_george)   "
            "a path to your own voice-over WAV   s = no voice-over")
        feedback = None
        if action == "approve":
            st["voice_ok"] = list(key)
            p.save()
            p.event("audio", "human_pick", "voice-over approved")
            return track, info
        if action == "pick" and val.lower() == "s":
            p.event("audio", "human_pick", "no voice-over")
            st["voice_off"] = True
            p.save()
            return None, "Voice-over switched off by you."
        if action == "pick" and val.lower().startswith("v "):
            st["voice"] = val[2:].strip()
            st.pop("voice_file", None)
            p.save()
            continue
        if action == "pick" and Path(val).exists():
            st["voice_file"] = str(Path(val).resolve())
            p.save()
            continue
        if action == "revise":
            p.log("audio", "human", f"voice-over: {val}")
            p.event("audio", "agent_feedback", f"voice-over: {val}")
            st.pop("voice_file", None)
            _rewrite_lines(ctx, script, times, val, wps)
            continue
        if action == "quit":
            raise SystemExit("Saved. Resume later.")


def _rewrite_lines(ctx, script, times, feedback, wps):
    p = ctx.project
    shots = [{"id": s["id"], "beat": s["beat"],
              "seconds": times[s["id"]][1],
              "narration": narration(script).get(s["id"])}
             for s in script["shots"]]
    new = ctx.llm.ask_json(LINES.format(wps=wps) + "\n" + history(ctx, "audio"),
                           f"Shots:\n{json.dumps(shots, indent=1)}\n\n"
                           f"Feedback: {feedback}")
    _apply_lines(p, script, new.get("narration"))


def _apply_lines(p, script, lines):
    if not lines:
        return
    by_id = {x["id"]: x.get("text") for x in lines if "id" in x}
    for s in script["shots"]:
        if s["id"] in by_id:
            s["narration"] = by_id[s["id"]]
    script["voiceover"] = " ".join(s["narration"] for s in script["shots"]
                                   if s.get("narration"))
    p.write_json("3_script.json", script)


def _generated(ctx, layer, total, acfg, st, adir):
    """Music or ambience: N options from ComfyUI, you pick one."""
    p, cfg = ctx.project, ctx.cfg
    lc = acfg.get(layer, {})
    n = lc.get("options", 2)
    wf = cfg["comfy"]["workflows"][layer]
    while True:
        prompt = st["prompts"][f"{layer}_prompt"]
        chosen = st.get(layer)
        if chosen and Path(chosen).exists():
            return Path(chosen)
        secs = math.ceil(total) + 2
        if layer == "ambience":
            secs = min(secs, lc.get("max_seconds", 20))  # looped to length
        print(f"\n[Audio agent] {layer}: generating {n} options ({secs}s) ...")
        p.set_status("audio", "working", f"generating {n} {layer} options")
        opts = []
        for i in range(n):
            frames = int(secs * 25) // 8 * 8 + 1          # LTX: 8k+1 frames
            files = ctx.comfy.run(wf, {
                "@PROMPT": prompt, "@SEED": random.randint(1, 2**31),
                "@DURATION": secs, "@FRAMES": frames,
                "@LYRICS": "[instrumental]",
                "@BPM": st["prompts"].get("bpm") or 90,
                "@FILENAME": f"studio/{p.dir.name}_{layer}"},
                adir / layer, label=f"{layer} #{i + 1}")
            got = _pick_files(files)
            if not got:
                raise RuntimeError(f"The {layer} workflow produced no audio "
                                   "file (needs a Save Audio node).")
            opts.append(got[0])
        previews = []
        for i, f in enumerate(opts, 1):
            fitted = fit_length(f, st["total"], adir / layer / f"opt{i}_fit.wav")
            previews.append(mix(st["video"], adir / f"preview_{layer}_{i}.mp4",
                                **{layer: fitted}))
        open_file(previews[0])
        listing = "\n".join(f"  #{i}: {v}" for i, v in enumerate(previews, 1))
        action, val = gate(
            f"{layer.capitalize()} options", f"Prompt: {prompt}\n"
            f"Previews (video + this {layer} only):\n{listing}", previews[0],
            f"Type 1-{n} to pick, t = new options (same prompt), a path to "
            f"your own {layer} file, s = no {layer}")
        if action == "pick" and val.isdigit() and 1 <= int(val) <= n:
            st[layer] = str(opts[int(val) - 1])
            p.save()
            p.event("audio", "human_pick", f"{layer}: picked option #{val}")
            return Path(st[layer])
        if action == "pick" and val.lower() == "t":
            p.event("audio", "human_pick", f"{layer}: new options")
            continue
        if action == "pick" and val.lower() == "s":
            st[f"{layer}_off"] = True
            p.save()
            p.event("audio", "human_pick", f"no {layer}")
            return None
        if action == "pick" and Path(val).exists():
            st[layer] = str(Path(val).resolve())
            p.save()
            return Path(st[layer])
        if action == "revise":
            p.log("audio", "human", f"{layer}: {val}")
            p.event("audio", "agent_feedback", f"{layer}: {val}")
            st["prompts"][f"{layer}_prompt"] = ctx.llm.ask_json(
                PROMPT.format(what=layer) + rules(ctx.cfg),
                f"Prompt:\n{prompt}\n\nFeedback: {val}")["prompt"]
            p.save()
            continue
        if action == "quit":
            raise SystemExit("Saved. Resume later.")
        print("Pick a number, t, s, a path, or r to revise.")


# ------------------------------------------------------------ stage
def run(ctx, feedback=None, items=None):
    p, cfg = ctx.project, ctx.cfg
    acfg = cfg.get("audio") or {}
    script = p.read_json("3_script.json")
    shots = script["shots"]
    video = p.dir / "final" / f"{p.dir.name}.mp4"
    if not video.exists():
        raise RuntimeError(f"No assembled video at {video}: approve Clips first.")
    adir = p.dir / "audio"
    adir.mkdir(exist_ok=True)
    st = p.state.setdefault("audio", {})
    st["video"], st["total"] = str(video), duration(video)
    total = st["total"]
    out = p.dir / "final" / f"{p.dir.name}_final.mp4"

    if not acfg.get("enabled", True):
        shutil.copy(video, out)
        return StageResult("Audio stage is switched off in config.yaml; "
                           f"final video copied: {out}", out,
                           {"final": str(out), "layers": []})

    times = timeline(ctx, shots)
    wps = acfg.get("voiceover", {}).get("max_words_per_second", 2.5)

    # ---- sound design (prompts) ----
    if not st.get("prompts"):
        p.set_status("audio", "working", "designing music and ambience")
        print("\n[Audio agent] designing the sound ...")
        plan = p.read_json("2_plan.json") or {}
        notes = p.read_json("5_edit_notes.json") or {}
        st["prompts"] = ctx.llm.ask_json(
            DESIGN + rules(cfg),
            f"Plan audio idea: {plan.get('audio_plan')}\nStyle: "
            f"{plan.get('style_line')}\nEditor's music note: "
            f"{notes.get('music')}\nVideo length: {total:.0f}s\n"
            f"Beats: {[s['beat'] for s in shots]}\n\n{history(ctx, 'audio')}")
        p.save()

    # ---- feedback from your review / the Master agent ----
    if feedback:
        p.set_status("audio", "working", "applying your feedback")
        cur = {"music_prompt": st["prompts"].get("music_prompt"),
               "ambience_prompt": st["prompts"].get("ambience_prompt"),
               "levels": st.get("levels") or {
                   "music": acfg.get("music", {}).get("level_db", -16),
                   "ambience": acfg.get("ambience", {}).get("level_db", -20)},
               "voice": st.get("voice"),
               "narration": [{"id": s["id"], "seconds": times[s["id"]][1],
                              "text": narration(script).get(s["id"])}
                             for s in shots]}
        r = ctx.llm.ask_json(REVISE.format(wps=wps) + "\n" + history(ctx, "audio"),
                             f"Current sound:\n{json.dumps(cur, indent=1)}\n\n"
                             f"Feedback: {feedback}")
        for k in ("music_prompt", "ambience_prompt"):
            if r.get(k):
                st["prompts"][k] = r[k]
        if r.get("levels"):
            st["levels"] = {**cur["levels"], **r["levels"]}
        if r.get("voice"):
            st["voice"] = r["voice"]
        if r.get("speed"):
            st["speed"] = r["speed"]
        _apply_lines(p, script, r.get("narration"))
        for layer in r.get("redo") or []:
            st.pop(layer, None)
            st.pop(f"{layer}_off", None)
            if layer == "voice":
                st.pop("voice_ok", None)
                st.pop("voice_file", None)
                st.pop("voice_off", None)
        p.save()
        print(f"[Audio agent] {r.get('summary', '')}")

    # ---- music / ambience that no longer fit the length ----
    for layer in ("music",):
        f = st.get(layer)
        if f and Path(f).exists() and duration(f) < total - 1:
            print(f"[Audio agent] {layer} is shorter than the video now; "
                  "making new options")
            st.pop(layer)

    # ---- the layers ----
    layers, notes = {}, []
    vc = acfg.get("voiceover", {})
    if vc.get("enabled", True) and not st.get("voice_off"):
        track, info = _voice(ctx, script, times, total, acfg, st, adir)
        if track:
            layers["voice"] = track
        notes.append(info)
    for layer in ("music", "ambience"):
        if acfg.get(layer, {}).get("enabled", True) and not st.get(f"{layer}_off"):
            if not Path(cfg["comfy"]["workflows"].get(layer, "")).exists():
                notes.append(f"{layer}: workflow file missing "
                             f"({cfg['comfy']['workflows'].get(layer)}), skipped")
                continue
            f = _generated(ctx, layer, total, acfg, st, adir)
            if f:
                layers[layer] = fit_length(f, total, adir / f"{layer}_fit.wav")
                notes.append(f"{layer}: {Path(f).name} | prompt: "
                             f"{st['prompts'][f'{layer}_prompt']}")

    # ---- final mix ----
    p.set_status("audio", "working", "mixing the final soundtrack")
    print("\n[Audio agent] mixing ...")
    lv = st.get("levels") or {}
    levels = {"voice": 0,
              "music": lv.get("music", acfg.get("music", {}).get("level_db", -16)),
              "ambience": lv.get("ambience",
                                 acfg.get("ambience", {}).get("level_db", -20)),
              "clip": acfg.get("clip_audio_db", -12)}
    if "voice" not in layers:          # music carries the video on its own
        shift = -levels["music"]
        levels = {k: v + shift for k, v in levels.items()}
    final = mix(video, out, voice=layers.get("voice"),
                music=layers.get("music"), ambience=layers.get("ambience"),
                clip_audio=acfg.get("keep_clip_audio", False), levels=levels,
                duck=acfg.get("duck_music", True),
                lufs=acfg.get("loudness_lufs", -14))
    open_file(final)
    st["final"] = str(final)
    p.save()
    used = [k for k in ("voice", "music", "ambience") if k in layers]
    summary = (f"Final video with sound: {final}\nLayers: "
               f"{', '.join(used) or 'none'} | levels (dB vs voice): music "
               f"{levels['music']:.0f}, ambience {levels['ambience']:.0f} | "
               f"loudness {acfg.get('loudness_lufs', -14)} LUFS\n\n"
               + "\n".join(notes))
    data = {"final": str(final), "layers": used, "levels": levels,
            "prompts": st["prompts"], "notes": notes,
            "narration": narration(script)}
    p.write_json("6_audio.json", data)
    return StageResult(summary, final, data)
