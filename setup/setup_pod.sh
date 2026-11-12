#!/bin/bash
# Fresh-pod setup for the Agentic AI Video Studio.
# Usage:  export HF_TOKEN=hf_...   [WITH_4K=1]   then   bash setup/setup_pod.sh
W=/workspace; C=$W/ComfyUI; M=$C/models; T=$W/dl_tmp
export HF_HUB_DISABLE_XET=1          # classic downloader: faster on many hosts
[ -z "$HF_TOKEN" ] && { echo "Set HF_TOKEN first: export HF_TOKEN=hf_..."; exit 1; }
# Only one setup at a time: a second copy would fight over download locks
exec 9>/tmp/studio_setup.lock
flock -n 9 || { echo "Setup is already running in another terminal; wait for it."; exit 1; }
# Clear leftovers from interrupted downloads (stale locks, partial files)
find $W -path "*/.cache/huggingface/download/*" \( -name "*.lock" -o -name "*.incomplete" \) -delete 2>/dev/null
FAILED=""
get() {  # repo pattern dir : one pattern per call, 3 tries
  for i in 1 2 3; do hf download "$1" --include "$2" --local-dir "$3" && return 0
    echo "retry $i: $2"; sleep 5; done; FAILED="$FAILED\n  $1 $2"; }
place() {  # move downloaded files from the temp folder into model folders
  for d in diffusion_models text_encoders vae; do mkdir -p "$M/$d"
    find "$T" -path "*/$d/*" -name "*.safetensors" -exec mv {} "$M/$d/" \; 2>/dev/null
  done; rm -rf "$T"; }

echo "== GPU, disk =="; nvidia-smi --query-gpu=name,memory.total --format=csv; df -h $W | tail -1
echo "== ComfyUI =="; mkdir -p $W && cd $W
[ -d $C ] || git clone https://github.com/comfyanonymous/ComfyUI.git
cd $C && pip install -r requirements.txt && pip install -U "huggingface_hub[cli]"

echo "== Z-Image Turbo (images) =="
Z=Comfy-Org/z_image_turbo
get $Z "*z_image_turbo_bf16*" $T
get $Z "*qwen_3_4b.safetensors" $T
get $Z "*vae/ae.safetensors" $T
place

echo "== LTX-2.5 (video) =="
L=Lightricks/LTX-2.5
get $L "*distilled-transformer-comfy-int8*" $M
get $L "*gemma4-12b*int8*" $M
get $L "vae/ltx-2.5-video-vae-bf16*" $M
get $L "vae/*audio-vae*" $M
get $L "*spatial-upscaler*" $M
get $L "*duration-head*" $M
get Comfy-Org/gemma-4 "*e2b_it_int8*" $M

echo "== ACE-Step 1.5 (music, all-in-one turbo checkpoint) =="
get Comfy-Org/ace_step_1.5_ComfyUI_files "checkpoints/ace_step_1.5_turbo_aio.safetensors" $M

echo "== Video node packs =="
cd $C/custom_nodes
[ -d ComfyUI-VideoHelperSuite ] || git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
pip install -r ComfyUI-VideoHelperSuite/requirements.txt
# LTX audio-only (text-to-audio) nodes for the ambience workflow
[ -d ComfyUI-LTXVideo ] || git clone https://github.com/Lightricks/ComfyUI-LTXVideo
[ -f ComfyUI-LTXVideo/requirements.txt ] && pip install -r ComfyUI-LTXVideo/requirements.txt
if [ "$WITH_4K" = "1" ]; then
  echo "== 4K / 60 fps tools =="
  [ -d ComfyUI-Frame-Interpolation ] || git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation
  pip install cupy-cuda12x && (cd ComfyUI-Frame-Interpolation && python install.py)
  get ai-forever/Real-ESRGAN "RealESRGAN_x2.pth" $M/upscale_models
fi

echo "== Studio dependencies =="
[ -f $W/agentic_studio/requirements.txt ] && pip install -r $W/agentic_studio/requirements.txt
which ffmpeg >/dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg)
echo "== Voice-over (Kokoro TTS) =="
which espeak-ng >/dev/null || (apt-get update -qq && apt-get install -y -qq espeak-ng)
pip install "kokoro>=0.9" soundfile
echo "== Dashboard voice (Whisper speech-to-text) =="
pip install faster-whisper

rm -rf $W/.cache/huggingface ~/.cache/huggingface/hub
echo "== Models =="; ls -lh $M/diffusion_models $M/text_encoders $M/vae 2>/dev/null | grep -v "^total"
df -h $W | tail -1
bash $W/agentic_studio/setup/start_comfy.sh
[ -n "$FAILED" ] && echo -e "FAILED (run this script again):$FAILED" || echo "ALL DOWNLOADS OK"
