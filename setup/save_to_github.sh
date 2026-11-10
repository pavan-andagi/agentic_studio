#!/bin/bash
# One command to save code, workflows and config to your private GitHub repo.
# Needs STUDIO_REPO and GITHUB_TOKEN (with write access) in the environment.
set -a; [ -f /workspace/studio.env ] && . /workspace/studio.env; set +a
S=/workspace/agentic_studio; cd $S || exit 1
[ -z "$STUDIO_REPO" ] || [ -z "$GITHUB_TOKEN" ] && { echo "Set STUDIO_REPO and GITHUB_TOKEN first"; exit 1; }
mkdir -p workflows/ui && cp /workspace/ComfyUI/user/default/workflows/*.json workflows/ui/ 2>/dev/null
[ -d .git ] || { git init -q && git branch -M main; }
git config user.name >/dev/null || git config user.name "studio"
git config user.email >/dev/null || git config user.email "studio@localhost"
git remote remove origin 2>/dev/null
git remote add origin "https://$GITHUB_TOKEN@github.com/$STUDIO_REPO.git"
git add -A && git commit -qm "studio update $(date +%F_%H%M)" || echo "nothing new to commit"
git push -u origin main && echo "SAVED to github.com/$STUDIO_REPO"
