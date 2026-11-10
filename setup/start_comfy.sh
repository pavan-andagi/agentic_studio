#!/bin/bash
# Start (or restart) ComfyUI and wait until it answers.
cd /workspace/ComfyUI || { echo "ComfyUI not installed"; exit 1; }
pkill -f "main.py --listen" && sleep 3
nohup python main.py --listen 0.0.0.0 --port 8188 > /workspace/comfyui.log 2>&1 &
for i in $(seq 1 24); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8188)" = "200" ] \
    && { echo "ComfyUI READY on port 8188"; exit 0; }; sleep 5; done
echo "ComfyUI NOT READY - last log lines:"; tail -n 20 /workspace/comfyui.log; exit 1
