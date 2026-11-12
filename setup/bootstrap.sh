#!/bin/bash
# Runs automatically on every new pod (from the provider's on-start script),
# or by hand:  bash /workspace/agentic_studio/setup/bootstrap.sh
# Settings come from environment variables (set them as secrets in your
# RunPod / Vast.ai template) or from /workspace/studio.env.
set -a; [ -f /workspace/studio.env ] && . /workspace/studio.env; set +a
W=/workspace; S=$W/agentic_studio; LOG=$W/bootstrap.log
exec > >(tee -a $LOG) 2>&1
echo; echo "################################################################"
echo "==== bootstrap $(date)  (pod ${RUNPOD_POD_ID:-?}) ===="
echo "################################################################"
for v in HF_TOKEN ANTHROPIC_API_KEY; do
  [ -z "${!v}" ] && echo "WARNING: $v is not set (see setup/studio.env.example)"
done

# 1. Get or update the studio code (+ your saved workflows) from GitHub
if [ -n "$STUDIO_REPO" ]; then
  URL="https://github.com/$STUDIO_REPO.git"
  [ -n "$GITHUB_TOKEN" ] && URL="https://$GITHUB_TOKEN@github.com/$STUDIO_REPO.git"
  if [ -d $S/.git ]; then git -C $S pull --ff-only; else git clone "$URL" $S; fi
fi
[ -d $S ] || { echo "No studio at $S: set STUDIO_REPO or upload it"; exit 1; }

# 2. Install ComfyUI, models, node packs, Python packages; start ComfyUI
bash $S/setup/setup_pod.sh

# 2b. Restore your ComfyUI workflow tabs (editable versions)
if [ -d $S/workflows/ui ]; then
  mkdir -p $W/ComfyUI/user/default/workflows
  cp -n $S/workflows/ui/*.json $W/ComfyUI/user/default/workflows/ 2>/dev/null
  echo "ComfyUI workflows restored: $(ls $S/workflows/ui | wc -l)"
fi

# 3. Restore your projects from cloud storage (optional, needs rclone)
if [ -n "$RCLONE_REMOTE" ]; then
  which rclone >/dev/null || (curl -s https://rclone.org/install.sh | bash)
  mkdir -p ~/.config/rclone
  [ -n "$RCLONE_CONFIG_B64" ] && echo "$RCLONE_CONFIG_B64" | base64 -d > ~/.config/rclone/rclone.conf
  rclone copy "$RCLONE_REMOTE/projects" $S/projects --progress
fi

# 4. Check the workflows the agents need
cd $S && python -m studio.check_workflows

# 5. Only say READY when everything really works on THIS pod
MISSING=$(python - <<'PY'
import importlib.util
mods = ["anthropic", "yaml", "requests", "PIL", "soundfile", "kokoro",
        "faster_whisper"]
print(" ".join(m for m in mods if importlib.util.find_spec(m) is None))
PY
)
COMFY=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8188)
if [ -z "$MISSING" ] && [ "$COMFY" = "200" ] && which ffmpeg espeak-ng >/dev/null; then
  echo "==== READY (pod ${RUNPOD_POD_ID:-?}, $(date +%H:%M)): cd $S && python -m studio.main resume <project>  (or new) ===="
else
  echo "==== NOT READY (pod ${RUNPOD_POD_ID:-?}) ===="
  [ -n "$MISSING" ] && echo "  missing Python packages: $MISSING  ->  pip install -r $S/requirements.txt \"kokoro>=0.9\" faster-whisper"
  [ "$COMFY" != "200" ] && echo "  ComfyUI not answering on 8188  ->  bash $S/setup/start_comfy.sh"
  which ffmpeg espeak-ng >/dev/null || echo "  missing ffmpeg or espeak-ng  ->  apt-get install -y ffmpeg espeak-ng"
fi
