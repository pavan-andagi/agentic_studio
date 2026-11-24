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
# `swap` is a model group, not a mode, and it may appear in either
# position. On its own it means "just fetch that group" - which is what
# anyone typing `setup_pod.sh swap` means, and it used to silently run a
# full install instead.
WANT_SWAP=0
ARGS=()
for a in "$@"; do
  if [ "$a" = swap ]; then WANT_SWAP=1; else ARGS+=("$a"); fi
done
MODE=${ARGS[0]:-all}
[ "$MODE" = all ] && [ "$WANT_SWAP" = 1 ] && [ ${#ARGS[@]} -eq 0 ] && MODE=models
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
diffusion_models|wan2.2_animate_14B_fp8_scaled.safetensors|https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/Wan22Animate/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors|?|swap
clip_vision|clip_vision_h.safetensors|https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors|?|swap
vae|wan_2.1_vae.safetensors|https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors|?|swap
text_encoders|umt5_xxl_fp8_e4m3fn_scaled.safetensors|https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors|?|swap
loras|lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors|https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors|?|swap
EOF
)

# Which groups this run should fetch.
#
# The `swap` group is Wan 2.2 Animate - the only thing here that can
# replace a character in a video and keep the original motion. It is
# opt-in because it is another ~30GB on top of everything else, and on a
# pod with no network volume that is 30GB re-fetched after every restart.
#
#   bash setup_pod.sh models swap    gets it
#   bash setup_pod.sh                does not
want_group() {
  case "$MODE" in
    cast)  [ "$1" = cast ] ;;
    *)     [ "$1" != swap ] || [ "${WANT_SWAP:-0}" = 1 ] ;;
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
    # aria2 is here for the models: HuggingFace throttles one connection,
    # and the download step uses aria2c when it finds it.
    apt-get install -y -qq ffmpeg espeak-ng libsndfile1 \
        fonts-dejavu-core aria2 2>/dev/null \
      && note "ffmpeg, espeak-ng, libsndfile1, DejaVu fonts, aria2" \
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
  nodes=( https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
          https://github.com/Lightricks/ComfyUI-LTXVideo.git
          https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git )
  # Wan 2.2 Animate needs these two. Only fetched with `swap`, because
  # every extra node pack is another unpinned repo that can break a pod.
  if [ "${WANT_SWAP:-0}" = 1 ]; then
    nodes+=( https://github.com/kijai/ComfyUI-KJNodes.git
             https://github.com/Fannovel16/comfyui_controlnet_aux.git )
  fi
  for r in "${nodes[@]}"; do
    d="$COMFY/custom_nodes/$(basename "$r" .git)"
    [ -d "$d" ] && { note "$(basename "$d") ok ($(git -C "$d" rev-parse --short HEAD 2>/dev/null))"; continue; }
    git clone --depth 1 "$r" "$d" && note "cloned $(basename "$d")"
  done
  [ -f "$COMFY/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt" ] && \
    $PIP -r "$COMFY/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt"
  [ -f "$COMFY/custom_nodes/ComfyUI-LTXVideo/requirements.txt" ] && \
    $PIP -r "$COMFY/custom_nodes/ComfyUI-LTXVideo/requirements.txt"
  if [ "${WANT_SWAP:-0}" = 1 ]; then
    for n in ComfyUI-KJNodes comfyui_controlnet_aux; do
      [ -f "$COMFY/custom_nodes/$n/requirements.txt" ] && \
        $PIP -r "$COMFY/custom_nodes/$n/requirements.txt"
    done
  fi
  # Frame-Interpolation's own install.py dies on cupy-wheel / pkg_resources
  note "frame-interpolation deps (skipping its install.py on purpose)"
  $PIP cupy-cuda12x einops opencv-contrib-python kornia scipy \
    || note "cupy install reported problems"

  mkdir -p "$COMFY/models"/{diffusion_models,text_encoders,vae,loras,checkpoints,upscale_models,latent_upscale_models,model_patches,clip_vision}
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
# A safetensors file carries an 8-byte header length, then a JSON header
# naming every tensor's byte range. If those ranges do not end exactly at
# the end of the file, the file is short or something was appended - and
# nothing downstream will tell you. Non-safetensors files are accepted as
# they are; there is nothing here to check.
safetensors_ok() {
  case "$1" in *.safetensors) ;; *) return 0 ;; esac
  python3 - "$1" <<'EOPY'
import json, struct, sys
p = sys.argv[1]
try:
    with open(p, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n <= 0 or n > 200_000_000:
            sys.exit(1)
        hdr = json.loads(f.read(n))
        end = max(v["data_offsets"][1] for k, v in hdr.items()
                  if k != "__metadata__")
        f.seek(0, 2)
        actual, want = f.tell(), 8 + n + end
        if actual != want:
            print(f"      header wants {want:,} bytes, file has {actual:,} "
                  f"({actual - want:+,})")
            sys.exit(1)
except Exception as e:
    print(f"      unreadable: {e}")
    sys.exit(1)
EOPY
}

# Every model file, checked. Fast - it reads a header, not the weights.
do_verify() {
  say "Model files"
  local bad_n=0 ok_n=0
  while IFS='|' read -r dir file url size group; do
    [ -z "${dir:-}" ] && continue
    local dest="$COMFY/models/$dir/$file"
    [ -s "$dest" ] || continue
    if safetensors_ok "$dest"; then
      note "ok     $file  ($(du -h "$dest" | cut -f1))"
      ok_n=$((ok_n+1))
    else
      bad "CORRUPT $file  ($(du -h "$dest" | cut -f1))"
      bad_n=$((bad_n+1))
    fi
  done <<< "$MODELS"
  note ""
  if [ "$bad_n" -gt 0 ]; then
    bad "$bad_n file(s) corrupt. Delete them and run:  bash setup_pod.sh models"
  else
    note "$ok_n file(s) check out"
  fi
}

do_models() {
  say "Models"
  local hdr=()
  [ -n "${HF_TOKEN:-}" ] && hdr=(--header="Authorization: Bearer $HF_TOKEN")
  local got=0 skip=0 fail=0
  while IFS='|' read -r dir file url size group; do
    [ -z "${dir:-}" ] && continue
    want_group "$group" || { note "skip   $file  (group: $group)"; continue; }
    local dest="$COMFY/models/$dir/$file"
    # "it exists" is not "it is right". A doubled qwen_3_4b.safetensors
    # cost an evening: ComfyUI loaded it without complaint and the text
    # encoder returned NaN, which decodes to a perfectly black image.
    if [ -s "$dest" ]; then
      if safetensors_ok "$dest"; then
        note "have   $file  ($(du -h "$dest" | cut -f1))"
        skip=$((skip+1)); continue
      fi
      bad "       ^ $file is corrupt ($(du -h "$dest" | cut -f1)) - refetching"
      rm -f "$dest"
    fi
    [ "$MODE" = check ] && { note "MISSING $dir/$file  (~$size)"; fail=$((fail+1)); continue; }
    note "get    $file  (~$size)$(command -v aria2c >/dev/null \
      && echo "  [aria2c, 8 connections]" || echo "  [wget, 1 connection]")"
    mkdir -p "$(dirname "$dest")"
    # Downloaded to .part and moved into place only once it checks out,
    # so an interrupted run leaves no file that looks finished. No -c:
    # wget cannot resume sensibly into -O, and a retry appends instead
    # of restarting, which is exactly how a file ends up twice its size.
    local part="$dest.part"
    rm -f "$part"
    # HuggingFace throttles a single connection hard - often a fifth of
    # what the pod can actually pull. aria2c opens several and is
    # routinely 5x faster on the big files. wget stays as the fallback
    # so nothing breaks where aria2c is missing.
    if command -v aria2c >/dev/null; then
      if aria2c -x 8 -s 8 -k 16M --continue=false --allow-overwrite=true \
           --summary-interval=10 --console-log-level=warn \
           ${HF_TOKEN:+--header="Authorization: Bearer $HF_TOKEN"} \
           -d "$(dirname "$part")" -o "$(basename "$part")" "$url"; then
        dl_ok=1
      else
        dl_ok=0
      fi
    elif wget -q --show-progress "${hdr[@]}" -O "$part" "$url"; then
      dl_ok=1
    else
      dl_ok=0
    fi
    if [ "$dl_ok" = 1 ]; then
      if [ "$(stat -c%s "$part")" -lt 100000 ]; then
        bad "       ^ got $(stat -c%s "$part") bytes - that is an error page, removing"
        head -c 200 "$part" | tr -d '\0' | tee -a "$LOG"
        rm -f "$part"; fail=$((fail+1))
      elif ! safetensors_ok "$part"; then
        bad "       ^ downloaded but the file does not check out - removing"
        rm -f "$part"; fail=$((fail+1))
      else
        mv -f "$part" "$dest"; got=$((got+1))
      fi
    else
      bad "       ^ FAILED (gated repo? export HF_TOKEN=hf_xxx)"
      rm -f "$part"; fail=$((fail+1))
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
    note "https://${pod}-8765.proxy.runpod.net/?key=${STUDIO_DASH_TOKEN}"
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
  verify) do_verify ;;
  deps)   do_deps; do_check ;;
  check)  do_check; do_models ;;
  models) do_models
          [ "$WANT_SWAP" = 1 ] && do_comfy
          do_check ;;
  nodes)  do_comfy ;;
  all)    do_deps; do_comfy; do_studio; do_models; do_check ;;
  *)      bad "unknown: $MODE"
          note ""
          note "  setup_pod.sh              everything except the swap models"
          note "  setup_pod.sh all swap     everything, swap models included"
          note "  setup_pod.sh swap         only the swap models and nodes"
          note "  setup_pod.sh models       models only"
          note "  setup_pod.sh nodes        ComfyUI and its custom nodes only"
          note "  setup_pod.sh deps         system and python packages only"
          note "  setup_pod.sh check        what is present, what is missing"
          note "  setup_pod.sh verify       are the model files intact"
          note "  setup_pod.sh url          the dashboard link"
          note "  setup_pod.sh stop         stop ComfyUI and the studio"
          exit 2 ;;
esac

say "Done - full log at $LOG"
