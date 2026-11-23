#!/usr/bin/env bash
# Start the studio GUI and nothing else.
#
#   bash start_gui.sh            # foreground; Ctrl-C stops it
#   bash start_gui.sh -d         # detached, log in /workspace/dash.log
#   bash start_gui.sh --no-comfy # skip the ComfyUI check
#
# There is no run attached. You choose what to make in the browser:
# Topic, Script, Script + pictures, One picture, or Describe a picture.
#
# The three things that have actually gone wrong, each handled here:
#   - no token, so the dashboard binds 127.0.0.1 and the RunPod proxy
#     URL answers "waiting for service to respond" forever
#   - port 8765 still held by a dashboard from before, often one
#     suspended with Ctrl-Z, which ignores SIGTERM
#   - a model file that downloaded wrong, which produces black images
#     and no error anywhere
set -uo pipefail

ROOT=${ROOT:-/workspace}
STUDIO=${STUDIO:-$ROOT/agentic_studio}
COMFY=${COMFY:-$ROOT/ComfyUI}
PORT=${STUDIO_DASH_PORT:-8765}
CPORT=${COMFY_PORT:-8188}

DETACH=0
CHECK_COMFY=1
for a in "$@"; do
  case "$a" in
    -d|--detach)    DETACH=1 ;;
    --no-comfy)     CHECK_COMFY=0 ;;
    -h|--help)      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
bad()  { printf '    \033[31m%s\033[0m\n' "$*"; }

cd "$STUDIO" || { bad "no studio at $STUDIO"; exit 1; }

# ---------------------------------------------------------------- token
# Without one the dashboard binds 127.0.0.1 only, and nothing outside the
# pod can reach it. A RunPod account Secret survives the pod; a generated
# one does not, so the link changes on every restart.
: "${STUDIO_DASH_TOKEN:=${RUNPOD_SECRET_STUDIO_DASH_TOKEN:-}}"
if [ -z "$STUDIO_DASH_TOKEN" ]; then
  STUDIO_DASH_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
  bad "STUDIO_DASH_TOKEN was not set, so this run gets a throwaway one."
  bad "The link will be different after every restart. To fix that for"
  bad "good, add a RunPod account Secret named STUDIO_DASH_TOKEN."
fi
export STUDIO_DASH_TOKEN

# ---------------------------------------------------------------- comfy
if [ "$CHECK_COMFY" = 1 ]; then
  say "ComfyUI"
  if curl -sf "localhost:$CPORT/system_stats" >/dev/null 2>&1; then
    note "already answering on $CPORT"
  else
    if [ -x "$STUDIO/start_comfy.sh" ] || [ -f "$STUDIO/start_comfy.sh" ]; then
      note "not running - starting it with start_comfy.sh"
      bash "$STUDIO/start_comfy.sh" >/dev/null 2>&1 &
    else
      note "not running - starting it"
      ( cd "$COMFY" && nohup python main.py --listen 0.0.0.0 \
          --port "$CPORT" --bf16-text-enc > "$ROOT/comfy.log" 2>&1 & )
    fi
    printf '    waiting'
    for _ in $(seq 1 60); do
      curl -sf "localhost:$CPORT/system_stats" >/dev/null 2>&1 && break
      printf '.'; sleep 3
    done
    echo
    if curl -sf "localhost:$CPORT/system_stats" >/dev/null 2>&1; then
      note "up on $CPORT"
    else
      bad "still not answering after 3 minutes - see $ROOT/comfy.log"
      bad "The GUI will start anyway, but no stage that needs a model"
      bad "will get past its first call."
    fi
  fi
fi

# ----------------------------------------------------------------- port
say "Port $PORT"
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  note "in use - stopping whatever holds it"
  # -9 because a process suspended with Ctrl-Z never acts on SIGTERM,
  # which looks exactly like the kill having done nothing.
  pkill -9 -f "studio.dashboard" 2>/dev/null
  pkill -9 -f "studio.main"      2>/dev/null
  sleep 1
  if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    bad "still held by something else. Find it with:  ss -ltnp | grep $PORT"
    exit 1
  fi
  note "free"
else
  note "free"
fi

# --------------------------------------------------------------- models
# Three seconds, and it is the check that would have saved an evening:
# a model file can download wrong, load without complaint, and produce
# black images with nothing in any log.
if [ -f "$STUDIO/setup_pod.sh" ]; then
  out=$(bash "$STUDIO/setup_pod.sh" verify 2>/dev/null | grep -i CORRUPT)
  if [ -n "$out" ]; then
    say "Models"
    bad "$out"
    bad "Delete those and run:  bash setup_pod.sh models"
    bad "Until then anything they touch comes out black or silent."
  fi
fi

# ------------------------------------------------------------------ run
say "Dashboard"
pod=${RUNPOD_POD_ID:-}
if [ -n "$pod" ]; then
  note "https://${pod}-${PORT}.proxy.runpod.net/?key=${STUDIO_DASH_TOKEN}"
else
  note "http://localhost:${PORT}/?key=${STUDIO_DASH_TOKEN}"
fi
note ""
note "Open it, click + New run, and choose what you are starting from."

if [ "$DETACH" = 1 ]; then
  nohup python -m studio.dashboard --root projects --port "$PORT" \
    > "$ROOT/dash.log" 2>&1 &
  note "detached, pid $!  - log: $ROOT/dash.log"
  note "stop it with:  pkill -9 -f studio.dashboard"
else
  note "Ctrl-C stops it."
  exec python -m studio.dashboard --root projects --port "$PORT"
fi
