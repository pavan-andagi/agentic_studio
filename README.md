# Agentic AI Video Studio

Five agents turn a channel brief into a finished video. **Every agent
stops for your approval** before the next one starts, and everything is
saved, so you can quit at any gate and resume later.

```
brief ─▶ 1 Trend Identifier ─▶ 2 Project Planner ─▶ 3 Script / Plot
                (web search)       (style + characters    (shot list +
                                    locked)                all prompts)
      ─▶ 4 Image Creation ─▶ 5 Clips + Finishing + Assembly + Edit notes
          (Z-Image Turbo,       (LTX-2.5 I2V / FLF2V, auto-checks,
           you pick per shot)    you approve per clip, optional 4K,
                                 joined video, editing suggestions)
```

| Agent | Uses | Your gate |
| --- | --- | --- |
| 1 Trend Identifier | Claude + web search | pick one of 6-8 ranked trends |
| 2 Project Planner | Claude | approve plan: platforms, style line, locked characters, beats, risks |
| 3 Script / Plot | Claude | approve shot list: modes, durations, prompts, bubbles, captions |
| 4 Image Creation | ComfyUI · Z-Image Turbo | pick 1 of N images per keyframe, then approve all together |
| 5 Clips & Editing | ComfyUI · LTX-2.5 (+ 4K workflow), ffmpeg, Claude | approve / retake / revise each clip, then approve the final cut + edit notes |

Inside the image and clip stages you also pick per item: a number, **t**
(new options, same prompt), **r** (rewrite the prompt from feedback) or a
path to your own file per keyframe; approve / **t** retake / **r** revise
per clip.

## How each stage works: you review, the Master decides

1. The **stage agent** produces its output.
2. **You review it.** Press Enter if it's good, or **type your feedback
   in your own words** ("the bride's face tilts in s01", "the tiger looks
   different in every shot", "make the music warmer").
3. The **Master agent** reads your feedback, the stage output, every
   approved decision and the feedback log, then traces the problem to
   its **root cause** and decides:
   - **proceed**: small notes are passed on to the next agent, or
   - **go back** to the **earliest stage that caused it**, limited to the
     affected shots, with precise instructions for that agent.
4. The orchestrator carries the decision out automatically: it re-opens
   that stage, the agent fixes it, and the pipeline continues from there
   (you review again at each stage on the way).

| Your feedback (example) | Master's typical decision |
| --- | --- |
| "Clip s03 jitters" | Clips: retake s03 |
| "Her face tilts in s01" | Script: add a head-lock line to s01's motion prompt; redo clip s01 (keyframe kept) |
| "s04 has the wrong background" | Images: regenerate s04's keyframe, then its clip |
| "The tiger looks different in every shot" | Plan: lock one tiger description; then script, images and clips follow |
| "Warmer music" | Proceed: saved as a note in the edit notes |

Rework is kept to a minimum: only affected shots are redone, keyframes
are reused when only a motion prompt changed, and clips chained after a
redone shot are refreshed automatically. Every comment, yours and the
Master's, goes into the **feedback log** (`state.json`), which all agents
read, so earlier decisions are never forgotten.

Options in `config.yaml`: `master.confirm_decisions: true` asks you to
confirm each Master decision before it runs; `master.enabled: false`
skips the Master (your feedback then simply redoes the current stage).

## Setup

On a fresh GPU pod (L40S class, 200 GB disk), upload this folder to
`/workspace/agentic_studio`, then:

    export HF_TOKEN=hf_...   # add WITH_4K=1 for the 4K/60 fps tools
    bash /workspace/agentic_studio/setup/setup_pod.sh

It installs ComfyUI, Z-Image Turbo, LTX-2.5, the video node packs and the
studio's Python packages, then starts ComfyUI. Restart ComfyUI any time
with `bash setup/start_comfy.sh`.

Then:


1. Python 3.10+, ffmpeg, then `pip install -r requirements.txt`
2. `export ANTHROPIC_API_KEY=...` (Claude API key)
3. ComfyUI running with your models; reachable at `comfy.url` in
   `config.yaml` (on the pod: `http://127.0.0.1:8188`; from a laptop:
   an SSH tunnel to 8188)
4. Export and tag your workflows: see `workflows/README.md`, then run
   `python -m studio.check_workflows`

## Run

```
python -m studio.main new tiger_bride --brief "Faceless Instagram/YouTube \
channel: cinematic Indian mythology and cute kids' moral stories, \
audience India + diaspora, 9:16 Reels and 16:9 YouTube."

python -m studio.main resume tiger_bride      # after quitting at a gate
```

## From the second run: automatic setup

1. Put this folder in a **private GitHub repo** and commit your three
   exported workflows in `workflows/` (projects and keys stay out: see
   `.gitignore`).
2. In your RunPod or Vast.ai **template**, add the values from
   `setup/studio.env.example` as **secret environment variables**, and
   this **on-start command**:

       cd /workspace && git clone https://$GITHUB_TOKEN@github.com/$STUDIO_REPO.git agentic_studio 2>/dev/null; bash /workspace/agentic_studio/setup/bootstrap.sh &

3. Deploy a pod from that template. It installs everything, restores your
   projects (if rclone is set), starts ComfyUI and checks the workflows;
   progress is in `/workspace/bootstrap.log`.
4. End of session: `bash setup/save_session.sh`, then destroy the pod.

## Dashboard: watch the agents work

Starting `python -m studio.main ...` also starts a live dashboard
(port 8765). Two views:

- **Flow**: the pipeline with live status per stage, the hand-off files,
  each stage's output (trends, plan, shot table, keyframes, clip players,
  final video), the Master's decisions, your feedback and the console.
  When a gate is waiting you can **answer it in the browser** (pick an
  image, approve, retake, send feedback); the terminal works too.
- **Communication**: a live sequence diagram of every message between
  You, the Orchestrator, the five agents, the Master, the Claude API and
  ComfyUI, with token counts, GPU job times and your decisions. Click a
  message for its details; filter by participant.

Open it from your laptop with an SSH tunnel to the pod:

    ssh -p <PORT> root@<IP> -L 8765:localhost:8765      then http://localhost:8765

or, in a RunPod template, expose HTTP port 8765 and set `STUDIO_DASH_TOKEN`
(then open `https://<pod-id>-8765.proxy.runpod.net/?key=<token>`).
Without a token the dashboard only listens on localhost.

Preview it without a pod: `python -m studio.dashboard --demo`
(browse any project folder later with `python -m studio.dashboard`).

## Project folder

```
projects/<name>/
  1_trends.json  1_trend_chosen.json  2_plan.json  3_script.json
  5_edit_notes.json   state.json (approvals, chosen keyframes, clips)
  candidates/  keyframes/  clips/  sheets/ (contact sheets)  final/
```

All JSON files are editable: change anything by hand at a gate, press
**e**, and the agent continues with your version.

## Notes

- Clips that continue the previous shot start from its last frame and
  drop the duplicate first frame when joined (no stutter).
- Auto-checks flag black frames, freezes and sudden jumps on every clip.
- 4K: set `defaults.upscale_4k: true` and provide `upscale_4k.json`
  (use Meta Batch Manager in it to avoid running out of RAM).
- Licenses: Z-Image Turbo and LTX-2.5 terms differ; the planner lists
  risks, but check each model's license before monetizing.

## Voice in the dashboard

In the review panel: **🎤 Speak** records your answer, Whisper on the pod
turns it into text (feedback goes into the box for you to check; short
commands like "approve", "option two", "retake", "skip" become the matching
button). **🔊 Listen** reads the open question aloud with Kokoro; the Master
tab can read its decision too. "Read new questions aloud" in the header
speaks every new review as it arrives.

- The microphone only works over **https** (the RunPod proxy link) or
  localhost; the browser asks for permission once.
- "Send clear commands right away" skips the extra click for commands;
  free-form feedback is always shown first so you can correct it.
- Without faster-whisper on the pod, Chrome/Edge fall back to the browser's
  own speech recognition (that sends your audio to the browser vendor).
