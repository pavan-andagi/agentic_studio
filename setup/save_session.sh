#!/bin/bash
# End of session: back up projects (and workflows) before destroying the pod.
set -a; [ -f /workspace/studio.env ] && . /workspace/studio.env; set +a
S=/workspace/agentic_studio; cd $S || exit 1
STAMP=$(date +%Y%m%d_%H%M)
# keep the editable ComfyUI workflows with the studio
mkdir -p workflows/ui && cp /workspace/ComfyUI/user/default/workflows/*.json workflows/ui/ 2>/dev/null
tar -czf /workspace/studio_backup_$STAMP.tar.gz projects workflows config.yaml 2>/dev/null
echo "Backup: /workspace/studio_backup_$STAMP.tar.gz (download it via Jupyter)"
if [ -n "$RCLONE_REMOTE" ]; then
  rclone copy projects "$RCLONE_REMOTE/projects" --progress && echo "Projects synced to $RCLONE_REMOTE"
fi
if [ -d .git ] && [ -n "$(git status --porcelain workflows config.yaml)" ]; then
  echo "Workflows or config changed: commit and push them so the next pod gets them:"
  echo "  git add workflows config.yaml && git commit -m 'update workflows' && git push"
fi
