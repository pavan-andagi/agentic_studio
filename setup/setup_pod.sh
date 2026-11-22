#!/usr/bin/env bash
# setup_pod.sh - bring a fresh RunPod pod up to a working studio.
#
#   bash setup_pod.sh              # everything
#   bash setup_pod.sh deps         # system + python packages only (fast)
#   bash setup_pod.sh cast         # only what the Cast stage needs (~40 GB)
#   bash setup_pod.sh models       # skip ComfyUI/repo, models only
#   bash setup_pod.sh check        # download nothing, just report
#   bash setup_pod.sh stop         # kill a stuck studio / ComfyUI and free ports
#
# Safe to re-run: every step skips work already done, and wget resumes
# a partial file instead of starting again.
#
# Gated repos: the LTX 2.5 files live in Lightricks/LTX-2.5, which asks
# you to accept a licence. Accept it once on huggingface.co, make a read
# token, then:  export HF_TOKEN=hf_xxx   before running this.

set -uo pipefail

# RunPod exposes account Secrets as RUNPOD_SECRET_<name>. Use them unless
# the plain variable is already set, so nothing has to be pasted by hand.
: "${HF_TOKEN:=${RUNPOD_SECRET_hf_token:-}}"
: "${GITHUB_TOKEN:=${RUNPOD_SECRET_github_token:-}}"
: "${ANTHROPIC_API_KEY:=${RUNPOD_SECRET_anthropic_key:-}}"
: "${STUDIO_DASH_TOKEN:=${RUNPOD_SECRET_STUDIO_DASH_TOKEN:-}}"
export HF_TOKEN GITHUB_TOKEN ANTHROPIC_API_KEY STUDIO_DASH_TOKEN

ROOT=${ROOT:-/workspace}
COMFY=$ROOT/ComfyUI
STUDIO=$ROOT/agentic_studio
REPO=${STUDIO_REPO:-pavan-andagi/agentic_studio}
BRANCH=${STUDIO_BRANCH:-video-style-selector}
MODE=${1:-all}
LOG=$ROOT/setup_pod.log
PIP="pip install -q --break-system-packages"

say()  { printf '\n\033[1m=== %s ===\033[0m\n' "$*" | tee -a "$LOG"; }
note() { printf '    %s\n' "$*" | tee -a "$LOG"; }
bad()  { printf '    \033[31m%s\033[0m\n' "$*" | tee -a "$LOG"; }

# ---------------------------------------------------------------- models
# folder|filename|url|approx size|group
MODELS=$(cat <<'EOF'
diffusion_models|z_image_turbo_bf16.safetensors|https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors|12G|cast
text_encoders|qwen_3_4b.safetensors|https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors|7.5G|cast
vae|ae.safetensors|https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors|320M|cast
diffusion_models|qwen_image_edit_2511_int8_convrot.safetensors|https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors|20G|cast
text_encoders|qwen_2.5_vl_7b_fp8_scaled.safetensors|https://huggingface.co/Comfy-Org/HunyuanVideo_1.5_repackaged/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors|8.8G|cast
vae|qwen_image_vae.safetensors|https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors|243M|cast
loras|Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors|https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors|811M|cast
diffusion_models|ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors|https://huggingface.co/Lightricks/LTX-2.5/resolve/main/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors|21G|video
text_encoders|gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors|https://huggingface.co/Lightricks/LTX-2.5/resolve/main/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors|15G|video
text_encoders|gemma4_e2b_it_int8_convrot.safetensors|https://huggingface.co/Comfy-Org/gemma-4/resolve/main/text_encoders/gemma4_e2b_it_int8_convrot.safetensors|4.9G|video
vae|ltx-2.5-video-vae-bf16.safetensors|https://huggingface.co/Lightricks/LTX-2.5/resolve/main/vae/ltx-2.5-video-vae-bf16.safetensors|1.4G|video
vae|ltx-2.5-audio-vae-bf16.safetensors|https://huggingface.co/Lightricks/LTX-2.5/resolve/main/vae/ltx-2.5-audio-vae-bf16.safetensors|348M|video
latent_upscale_models|ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors|https://huggingface.co/Lightricks/LTX-2.5/resolve/main/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors|?|video
model_patches|ltx-2.5-duration-head-bf16.safetensors|https://huggingface.co/Lightricks/LTX-2.5/resolve/main/model_patches/ltx-2.5-duration-head-bf16.safetensors|?|video
checkpoints|ace_step_1.5_turbo_aio.safetensors|https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/checkpoints/ace_step_1.5_turbo_aio.safetensors|9.4G|audio
upscale_models|RealESRGAN_x2.pth|https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x2.pth|64M|extra
EOF
)

want_group() {                       # which groups this run should fetch
  case "$MODE" in
    cast)  [ "$1" = cast ] ;;
    *)     true ;;
  esac
}

# ------------------------------------------------------------------ deps
# Everything the studio needs that is NOT a model. Each of these has cost
# a live run at some point:
#   ffmpeg/ffprobe  - the Finish and Render stages and the whole audio mix
#   espeak-ng       - Kokoro will not speak without it
#   libsndfile1     - soundfile, which Kokoro writes its WAVs through
#   a TTF font      - render.py draws captions with PIL and hunts the
#                     filesystem for a face; with none it falls back to a
#                     bitmap font and the type comes out tiny
do_deps() {
  say "System packages"
  if command -v apt-get >/dev/null; then
    apt-get update -qq 2>/dev/null
    apt-get install -y -qq ffmpeg espeak-ng libsndfile1 \
        fonts-dejavu-core 2>/dev/null \
      && note "ffmpeg, espeak-ng, libsndfile1, DejaVu fonts" \
      || bad "apt-get reported problems - check the log"
  else
    note "no apt-get here; install ffmpeg, espeak-ng, libsndfile1 and a TTF font yourself"
  fi

  say "Python packages"
  # studio itself
  $PIP anthropic requests pyyaml numpy pillow \
    && note "anthropic requests pyyaml numpy pillow" \
    || bad "pip reported problems"
  # voice-over. kokoro pulls torch deps, so it goes last and on its own
  $PIP soundfile && note "soundfile" || bad "soundfile FAILED"
  $PIP 'kokoro>=0.9' && note "kokoro" \
    || bad "kokoro FAILED - the Audio stage will not speak"
}

# --------------------------------------------------------------- comfyui
do_comfy() {
  say "ComfyUI"
  if [ -d "$COMFY/.git" ]; then
    note "already present ($(git -C "$COMFY" rev-parse --short HEAD))"
  else
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY" \
      || { bad "clone FAILED"; return 1; }
  fi
  note "installing python requirements (fresh pod = fresh site-packages)"
  $PIP -r "$COMFY/requirements.txt" || note "pip reported problems"

  mkdir -p "$COMFY/custom_nodes"
  # Existing nodes are left alone on purpose. An unpinned `git pull` here
  # once dragged in a major diffusers bump mid-session; update a node
  # deliberately, not as a side effect of running setup.
  for r in \
      https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
      https://github.com/Lightricks/ComfyUI-LTXVideo.git \
      https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git ; do
    d="$COMFY/custom_nodes/$(basename "$r" .git)"
    [ -d "$d" ] && { note "$(basename "$d") ok ($(git -C "$d" rev-parse --short HEAD 2>/dev/null))"; continue; }
    git clone --depth 1 "$r" "$d" && note "cloned $(basename "$d")"
  done
  [ -f "$COMFY/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt" ] && \
    $PIP -r "$COMFY/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt"
  [ -f "$COMFY/custom_nodes/ComfyUI-LTXVideo/requirements.txt" ] && \
    $PIP -r "$COMFY/custom_nodes/ComfyUI-LTXVideo/requirements.txt"
  # Frame-Interpolation's own install.py dies on cupy-wheel / pkg_resources
  note "frame-interpolation deps (skipping its install.py on purpose)"
  $PIP cupy-cuda12x einops opencv-contrib-python kornia scipy \
    || note "cupy install reported problems"

  mkdir -p "$COMFY/models"/{diffusion_models,text_encoders,vae,loras,checkpoints,upscale_models,latent_upscale_models,model_patches}
}

# ----------------------------------------------------------------- studio
do_studio() {
  say "agentic_studio ($BRANCH)"
  if [ -d "$STUDIO/.git" ]; then
    git -C "$STUDIO" fetch --all -q
    git -C "$STUDIO" checkout "$BRANCH" -q && git -C "$STUDIO" pull -q
    note "on $(git -C "$STUDIO" rev-parse --abbrev-ref HEAD) @ $(git -C "$STUDIO" log --oneline -1)"
  else
    local url="https://github.com/$REPO.git"
    [ -n "${GITHUB_TOKEN:-}" ] && url="https://$GITHUB_TOKEN@github.com/$REPO.git"
    git clone -q -b "$BRANCH" "$url" "$STUDIO" \
      && note "cloned $REPO ($BRANCH)" || bad "clone FAILED - private repo needs GITHUB_TOKEN"
  fi
}

# ----------------------------------------------------------------- models
do_models() {
  say "Models"
  local hdr=()
  [ -n "${HF_TOKEN:-}" ] && hdr=(--header="Authorization: Bearer $HF_TOKEN")
  local got=0 skip=0 fail=0
  while IFS='|' read -r dir file url size group; do
    [ -z "${dir:-}" ] && continue
    want_group "$group" || { note "skip   $file  (group: $group)"; continue; }
    local dest="$COMFY/models/$dir/$file"
    if [ -s "$dest" ]; then
      note "have   $file  ($(du -h "$dest" | cut -f1))"
      skip=$((skip+1)); continue
    fi
    [ "$MODE" = check ] && { note "MISSING $dir/$file  (~$size)"; fail=$((fail+1)); continue; }
    note "get    $file  (~$size)"
    mkdir -p "$(dirname "$dest")"
    if wget -q --show-progress -c "${hdr[@]}" -O "$dest" "$url"; then
      # an HTML error page is small; a real model is not
      if [ "$(stat -c%s "$dest")" -lt 100000 ]; then
        bad "       ^ got $(stat -c%s "$dest") bytes - that is an error page, removing"
        head -c 200 "$dest" | tr -d '\0' | tee -a "$LOG"
        rm -f "$dest"; fail=$((fail+1))
      else
        got=$((got+1))
      fi
    else
      bad "       ^ FAILED (gated repo? export HF_TOKEN=hf_xxx)"
      rm -f "$dest"; fail=$((fail+1))
    fi
  done <<< "$MODELS"
  note ""
  note "downloaded $got, already had $skip, failed/missing $fail"
}

# ------------------------------------------------------------------ stop
# Two traps that have cost real time:
#   - Ctrl-Z suspends the studio instead of ending it. A stopped process
#     cannot act on SIGTERM, so pkill looks like it did nothing while the
#     dashboard port stays bound.
#   - ComfyUI started as `cd ComfyUI && python main.py` has no "ComfyUI/"
#     in its command line, so `pkill -f ComfyUI/main.py` never matches it.
do_stop() {
  say "Stopping studio and ComfyUI"
  pkill -9 -f "studio.main"     && note "studio killed"   || note "no studio running"
  pkill -9 -f "main.py --listen" && note "ComfyUI killed" || note "no ComfyUI running"
  sleep 1
  for port in 8188 8765; do
    if ss -ltn 2>/dev/null | grep -q ":$port "; then
      bad "port $port still bound:"
      ss -ltnp 2>/dev/null | grep ":$port " | tee -a "$LOG"
    else
      note "port $port free"
    fi
  done
}

# -------------------------------------------------------------------- url
# The one thing you want after a restart: where the dashboard is.
#
# The pod id changes every restart, so the host is built from RUNPOD_POD_ID
# rather than remembered. The token must NOT change, or every restart hands
# you a different link - so keep it in a RunPod account Secret named
# STUDIO_DASH_TOKEN and it survives the pod being destroyed.
do_url() {
  local pod="${RUNPOD_POD_ID:-}"
  say "Dashboard"
  if [ -z "$pod" ]; then
    bad "RUNPOD_POD_ID is not set - not a RunPod pod? Then it is"
    bad "  http://localhost:8765/"
  elif [ -z "${STUDIO_DASH_TOKEN:-}" ]; then
    bad "STUDIO_DASH_TOKEN is not set, so the dashboard binds 127.0.0.1 only"
    bad "and the proxy URL will not reach it. Add a RunPod account Secret"
    bad "named STUDIO_DASH_TOKEN, then restart the pod."
  else
    note "https://${pod}-8765.proxy.runpod.net/?token=${STUDIO_DASH_TOKEN}"
  fi
  note "ComfyUI: https://${pod:-localhost}-8188.proxy.runpod.net/"
}

# ----------------------------------------------------------------- report
do_check() {
  say "State"
  for v in HF_TOKEN GITHUB_TOKEN ANTHROPIC_API_KEY STUDIO_DASH_TOKEN; do
    [ -n "${!v:-}" ] && note "$v: set" || bad "$v: NOT SET"
  done
  note ""
  note "disk:  $(df -h "$ROOT" | tail -1)"
  if mount | grep -q " $ROOT "; then
    note "mount: $(mount | grep " $ROOT " | head -1)"
  else
    bad "mount: $ROOT is CONTAINER DISK - everything here is lost when the pod stops"
  fi

  say "Tools"
  for t in ffmpeg ffprobe espeak-ng; do
    command -v "$t" >/dev/null && note "$t: $(command -v "$t")" || bad "$t: MISSING"
  done
  local font
  font=$(find /usr/share/fonts -name '*.ttf' 2>/dev/null | head -1)
  [ -n "$font" ] && note "font: $font" \
    || bad "font: NONE - captions will fall back to a tiny bitmap face"

  say "Python"
  python - <<'PY' 2>&1 | tee -a "$LOG"
for m in ("yaml", "anthropic", "requests", "numpy", "PIL",
          "soundfile", "kokoro"):
    try:
        __import__(m)
        print(f"    {m}: ok")
    except Exception as e:                                   # noqa: BLE001
        print(f"    {m}: MISSING  ({type(e).__name__})")
PY

  say "Models on disk"
  du -sh "$COMFY/models"/* 2>/dev/null | grep -v '\s0\s' | tee -a "$LOG"

  if [ -d "$STUDIO/studio" ]; then
    say "Workflows"
    (cd "$STUDIO" && python -m studio.check_workflows 2>&1 | tail -20 | tee -a "$LOG")
    say "Pipeline"
    (cd "$STUDIO" && python -c "
try:
    from studio.approval import STAGES
    print('    stages:', ' -> '.join(STAGES))
except Exception as e:
    print('    could not import the pipeline:', e)" 2>&1 | tee -a "$LOG")
  elif [ -d "$STUDIO" ]; then
    bad "$STUDIO exists but has no studio/ package - wrong branch or a bad clone"
  fi

  say "Running"
  pgrep -fa "studio.main"      >/dev/null && note "studio: running"  || note "studio: not running"
  if curl -sf localhost:8188/system_stats >/dev/null 2>&1; then
    note "comfy:  up on 8188"
    # The ambience layer needs this node; an older ComfyUI-LTXVideo
    # answers {} and the Audio stage dies halfway through.
    if curl -s localhost:8188/object_info/LTXVAudioOnlyModel | grep -q LTXVAudioOnlyModel; then
      note "comfy:  LTXVAudioOnlyModel present (ambience will work)"
    else
      bad "comfy:  LTXVAudioOnlyModel MISSING - update ComfyUI-LTXVideo and restart ComfyUI,"
      bad "        or set audio.ambience.enabled: false in config.yaml"
    fi
  else
    note "comfy:  not reachable on 8188"
  fi

  say "Start ComfyUI with"
  note "cd $COMFY && nohup python main.py --listen 0.0.0.0 --port 8188 > $ROOT/comfy.log 2>&1 &"

  do_url
}

# ------------------------------------------------------------------- main
mkdir -p "$ROOT"
printf '\n\n########## setup_pod.sh %s  mode=%s ##########\n' "$(date -Is)" "$MODE" >> "$LOG"

case "$MODE" in
  stop)   do_stop ;;
  url)    do_url ;;
  deps)   do_deps; do_check ;;
  check)  do_check; do_models ;;
  models) do_models; do_check ;;
  *)      do_deps; do_comfy; do_studio; do_models; do_check ;;
esac

say "Done - full log at $LOG"
