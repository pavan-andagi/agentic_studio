#!/usr/bin/env bash
# setup_pod.sh - bring a fresh RunPod pod up to a working studio.
#
#   bash setup_pod.sh              # everything
#   bash setup_pod.sh cast         # only what the Cast stage needs (~40 GB)
#   bash setup_pod.sh models       # skip ComfyUI/repo, models only
#   bash setup_pod.sh check        # download nothing, just report
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
BRANCH=${STUDIO_BRANCH:-cast-dev}
MODE=${1:-all}
LOG=$ROOT/setup_pod.log

say()  { printf '\n\033[1m=== %s ===\033[0m\n' "$*" | tee -a "$LOG"; }
note() { printf '    %s\n' "$*" | tee -a "$LOG"; }

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

# --------------------------------------------------------------- comfyui
do_comfy() {
  say "ComfyUI"
  if [ -d "$COMFY/.git" ]; then
    note "already present"
  else
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY" \
      || { note "clone FAILED"; return 1; }
  fi
  note "installing python requirements (fresh pod = fresh site-packages)"
  pip install -q -r "$COMFY/requirements.txt" || note "pip reported problems"

  mkdir -p "$COMFY/custom_nodes"
  for r in \
      https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
      https://github.com/Lightricks/ComfyUI-LTXVideo.git \
      https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git ; do
    d="$COMFY/custom_nodes/$(basename "$r" .git)"
    [ -d "$d" ] && { note "$(basename "$d") ok"; continue; }
    git clone --depth 1 "$r" "$d" && note "cloned $(basename "$d")"
  done
  [ -f "$COMFY/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt" ] && \
    pip install -q -r "$COMFY/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt"
  # Frame-Interpolation's own install.py dies on cupy-wheel / pkg_resources
  note "frame-interpolation deps (skipping its install.py on purpose)"
  pip install -q cupy-cuda12x einops opencv-contrib-python kornia scipy \
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
      && note "cloned $REPO ($BRANCH)" || note "clone FAILED - private repo needs GITHUB_TOKEN"
  fi
  pip install -q anthropic requests pyyaml 2>/dev/null
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
        note "       ^ got $(stat -c%s "$dest") bytes - that is an error page, removing"
        head -c 200 "$dest" | tr -d '\0' | tee -a "$LOG"
        rm -f "$dest"; fail=$((fail+1))
      else
        got=$((got+1))
      fi
    else
      note "       ^ FAILED (gated repo? export HF_TOKEN=hf_xxx)"
      rm -f "$dest"; fail=$((fail+1))
    fi
  done <<< "$MODELS"
  note ""
  note "downloaded $got, already had $skip, failed/missing $fail"
}

# ----------------------------------------------------------------- report
do_check() {
  say "State"
  for v in HF_TOKEN GITHUB_TOKEN ANTHROPIC_API_KEY STUDIO_DASH_TOKEN; do
    [ -n "${!v:-}" ] && note "$v: set" || note "$v: NOT SET"
  done
  note ""
  note "disk:  $(df -h "$ROOT" | tail -1)"
  if mount | grep -q " $ROOT "; then
    note "mount: $(mount | grep " $ROOT " | head -1)"
  else
    note "mount: $ROOT is CONTAINER DISK - everything here is lost when the pod stops"
  fi
  note ""
  du -sh "$COMFY/models"/* 2>/dev/null | grep -v '\s0\s' | tee -a "$LOG"
  if [ -d "$STUDIO" ]; then
    say "Workflows"
    (cd "$STUDIO" && python -m studio.check_workflows 2>&1 | tee -a "$LOG")
  fi
  say "Start ComfyUI with"
  note "cd $COMFY && nohup python main.py --listen 0.0.0.0 --port 8188 > $ROOT/comfy.log 2>&1 &"
}

# ------------------------------------------------------------------- main
mkdir -p "$ROOT"
printf '\n\n########## setup_pod.sh %s  mode=%s ##########\n' "$(date -Is)" "$MODE" >> "$LOG"

case "$MODE" in
  check)  do_check; do_models ;;
  models) do_models; do_check ;;
  *)      do_comfy; do_studio; do_models; do_check ;;
esac

say "Done - full log at $LOG"
