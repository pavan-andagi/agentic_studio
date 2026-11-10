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
| `upscale_4k.json` | your 1080p→4K workflow: Load Video (Upload) + Meta Batch Manager (30) → RIFE ×2 → Real-ESRGAN ×2 → Video Combine (h264-mp4, 60 fps, crf 16) |

Test each workflow once by hand in ComfyUI before exporting.

## 3. Quick check

    python -m studio.check_workflows

lists the tags found in each file, so you can see nothing is missing.
