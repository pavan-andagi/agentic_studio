# Preparing the ComfyUI workflows

The agents drive your own, tested ComfyUI workflows. Each one is exported
in **API format** with a few nodes **renamed with @tags**, so the agents
know where to put the prompt, seed, images and so on.

## 1. Tag the nodes (rename their titles)

Right-click a node → **Title**, and type the tag. Nodes inside a packed
subgraph count too: open it with ⤢ and tag the inner nodes. One node can
carry several tags, separated by spaces (e.g. `@WIDTH @HEIGHT`).

| Tag | Put it on | Filled with |
| --- | --- | --- |
| `@PROMPT` | the positive text-encode node | image or motion prompt |
| `@NEGATIVE` | the negative text-encode node (optional) | negative prompt |
| `@SEED` | the sampler / noise node with the seed | random seed per take |
| `@WIDTH @HEIGHT` | the empty-latent node (images) | size from config |
| `@IMAGE` | **Load Image** (start frame) | uploaded keyframe |
| `@LAST_IMAGE` | **Load Image** (last frame, FLF2V only) | uploaded keyframe |
| `@DURATION` | the LTX node with `duration` | seconds from the script |
| `@FPS` | the LTX node with `frame_rate` | fps from config |
| `@VIDEO` | **Load Video (Upload) 🎥🅥🅗🅢** (upscale workflow) | uploaded clip |
| `@FILENAME` | **Save Image / Save Video / Video Combine** | output name |
| `@LYRICS` | the ACE-Step text-encode node (music) | `[instrumental]` |
| `@BPM` | the ACE-Step node with `bpm` (if it has one) | tempo from the Audio agent |
| `@FRAMES` | the LTX audio latent node with `length` in frames (ambience) | seconds × 25, as 8k+1 |

For the audio workflows: `@PROMPT` on the ACE-Step encoder fills its
`tags` field; `@DURATION` fills `seconds`/`duration`; `@FILENAME` goes on
**Save Audio**. On the LTX audio workflow, tag the length with `@FRAMES`,
**not** `@DURATION` (that node counts frames, not seconds).

Tags that a workflow doesn't contain are simply left at the workflow's own
settings (the console prints a note). For example, if LTX's size comes
from a Resolution Selector, set it there once (9:16, 2.0 MP) and don't
tag width/height in the video workflows.

## 2. Export

**Workflow → Export (API)**, saved as:

| File | Built from |
| --- | --- |
| `z_image_t2i.json` | Z-Image Turbo: Text to Image |
| `ltx_i2v.json` | LTX-2.5: Image to Video (Int8) |
| `ltx_flf2v.json` | LTX-2.5: FLF2V (Int8) |
| `ace_step_music.json` | ComfyUI template **ACE-Step 1.5 Music Generation AIO** (uses `ace_step_1.5_turbo_aio.safetensors`) |
| `ltx_t2a.json` | LTX **Text-to-Audio** workflow (ComfyUI-LTXVideo, `LTXV Audio Only Model`), switched to your int8 distilled LTX-2.5 model |
| `upscale_4k.json` | your 1080p→4K workflow: Load Video (Upload) + Meta Batch Manager (30) → RIFE ×2 → Real-ESRGAN ×2 → Video Combine (h264-mp4, 60 fps, crf 16) |

Test each workflow once by hand in ComfyUI before exporting.

## 3. Quick check

    python -m studio.check_workflows

lists the tags found in each file, so you can see nothing is missing.

## qwen_edit_2511.json (Cast stage: consistent characters)

Built from the ComfyUI template **Qwen Image Edit 2511** (Workflow →
Browse Templates → Image). Models (setup_pod.sh downloads them):
`qwen_image_edit_2511_fp8mixed.safetensors` (diffusion_models),
`qwen_2.5_vl_7b_fp8_scaled.safetensors` (text_encoders),
`qwen_image_vae.safetensors` (vae),
`Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` (loras).

1. In **Load Diffusion Model** pick the `fp8mixed` file (the template
   defaults to bf16, which is not downloaded).
2. Turn the 4-step Lightning LoRA **on** (steps 4, CFG 1.0): multi-reference
   edits are cleaner with it.
3. Make sure there are **three Load Image nodes** feeding image1, image2,
   image3 of the positive (and negative) TextEncodeQwenImageEditPlus nodes.
   The studio removes the unused ones per shot automatically.
4. Tag (inside packed nodes, if any):

| Node | Title |
| --- | --- |
| Load Image → image1 | `@IMAGE` |
| Load Image → image2 | `@IMAGE2` |
| Load Image → image3 | `@IMAGE3` |
| positive TextEncodeQwenImageEditPlus | `@PROMPT` |
| KSampler | `@SEED` |
| Save Image | `@FILENAME` |
| EmptySD3LatentImage (if the template has one) | `@WIDTH @HEIGHT` |

If the latent comes from VAE Encode of image 1 instead, the output keeps the
shape of image 1; the cast pictures are made at the video's shape, so that
is fine.
5. Export (API) → `workflows/qwen_edit_2511.json`, and Export →
   `workflows/ui/qwen_edit_2511.json`.
