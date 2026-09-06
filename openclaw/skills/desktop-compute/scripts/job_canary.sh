#!/usr/bin/env bash
# Non-sensitive end-to-end canary for the restricted desktop job interface.
set -euo pipefail

mkdir -p outputs
gpu_name=$(
  /usr/lib/wsl/lib/nvidia-smi \
    --query-gpu=name \
    --format=csv,noheader \
    | head -n 1
)
case "$gpu_name" in
  *"RTX 5090"*) ;;
  *) exit 1 ;;
esac

jq -n \
  --arg result passed \
  --arg gpu "$gpu_name" \
  '{result: $result, gpu: $gpu}' \
  > outputs/canary.json
printf 'desktop-compute canary passed\n'
